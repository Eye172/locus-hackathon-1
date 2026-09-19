"""Frames from the middle of a short video, instead of the cover someone designed for it.

A TikTok cover is a poster: the title in big letters, a face, an arrow. The AI inspector rejects it, and rightly so.
Yet the video behind that cover is often the only footage of the campus that exists anywhere - the atrium with its
palm trees, a corridor between classes, the queue at the canteen - filmed by a student with no reason to stage it.
Seeking into the clip gets that footage for the price of one small download.

Which moments: the audit of 19 Sep (48 profiles) found social frames rejected mostly as "poster with text" (1 207),
"map" (667), "screenshot" (414) and "portrait" (339) - two fixed seeks (a third in, two thirds in) landed on a caption
card, a face talking to the camera or the app's interface as often as on the place. So the clip is sampled at eight
moments (four for a remote reel, where every seek is a network round trip) and the frames are chosen the way the
pipeline will judge them: CLIP's own place-versus-junk reading (the same classifier that rejects them later), sharpness
and variety - the best two or three frames that do not show the same view twice, and none at all from a clip whose
every moment is a talking head or a text card (the inspector does not pay for those).

The frames are written to `data/frames` and handed to the pipeline as `file:` candidates, so they go through the
same funnel as every other photo: hashing, de-duplication, the inspector, the confidence score.

Without ffmpeg on the machine this module quietly returns nothing and the sources fall back to covers.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageStat

from .. import http
from ..config import settings

log = logging.getLogger("campuslens.frames")

FFMPEG = shutil.which("ffmpeg")

# set by the orchestrator around each source: the moment its results are due. Searches fan out to many queries and
# clips; whatever has finished by then is returned, the rest is dropped - a source that runs long still contributes.
DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("search_deadline", default=None)


async def until_deadline(coros: list, margin: float = 0.0) -> list:
    """Runs coroutines side by side; results of the ones done by DEADLINE - margin, None for the rest."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    dl = DEADLINE.get()
    timeout = None if dl is None else max(0.5, dl - margin - time.monotonic())
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for t in pending:
        t.cancel()
    return [t.result() if t in done and not t.cancelled() and t.exception() is None else None for t in tasks]
FRACTIONS = (0.35, 0.7)     # the old two fixed seeks: frames cut before 19 Sep are named after them
SAMPLES = (0.1, 0.2, 0.32, 0.44, 0.56, 0.68, 0.8, 0.9)   # a downloaded clip: seeks are cheap
REMOTE_SAMPLES = (0.2, 0.42, 0.64, 0.86)                  # a reel read over the network: every seek is a round trip
SAME_VIEW = 0.93            # CLIP cosine above which two frames show the same view
# A frame is worth sending when it shows the place rather than the person filming: CLIP read against these two sets
# (zero-shot, the same for every clip). The pipeline's own junk classes miss the commonest failure - a student
# talking to the camera in a room reads to CLIP as "student life".
PLACE = ("a photo of a university building from outside", "a campus courtyard or square with trees",
         "a wide view of a hallway or atrium inside a university building", "a lecture hall or classroom",
         "a library reading room with bookshelves", "a university dormitory room", "a sports hall or stadium",
         "a canteen or cafeteria", "students at an event in a large hall", "a view of a campus from above")
VLOG = ("a selfie of a person talking to the camera", "a close-up of a person's face", "a person filming themselves with a phone",
        "white text on a black background", "a title card with large text", "a logo or emblem",
        "a screenshot of a phone app", "a blurry dark frame", "a person posing for a photo in front of a wall",
        "a collage of several photos", "a motion-blurred frame from a moving phone")
MIN_PLACE = 0.5             # the share of the reading that must go to the place
MAX_BYTES = 25_000_000      # a 60 s vertical clip is ~3 MB; a 3-minute dorm tour ~20 MB is still worth it
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"}


def _headers(url: str) -> dict:
    # TikTok's CDN wants to see its own site as the referrer; Instagram's CDN does not care and must not be told
    # it is TikTok
    return {**UA, "Referer": "https://www.tiktok.com/"} if "tiktok" in url else UA


def frames_dir() -> Path:
    d = settings.data_dir / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def available() -> bool:
    return FFMPEG is not None


def _grab(video: Path, duration_s: float, out_paths: list[Path], fracs=SAMPLES) -> list[Path]:
    """Runs in a worker thread: one ffmpeg seek per frame (fast, the file is already local)."""
    out: list[Path] = []
    for frac, dest in zip(fracs, out_paths):
        try:
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{duration_s * frac:.2f}", "-i", str(video),
                            "-frames:v", "1", "-q:v", "3", str(dest)],
                           check=False, timeout=8, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("ffmpeg failed: %r", e)
            continue
        if dest.exists() and dest.stat().st_size > 4000:
            out.append(dest)
    return out


_locks: dict[str, asyncio.Lock] = {}


def paths_for(key: str, fracs=SAMPLES) -> list[Path]:
    return [frames_dir() / f"{key}_{int(f * 100)}.jpg" for f in fracs]


def _choice_file(key: str) -> Path:
    return frames_dir() / f"{key}.json"


def ready(key: str) -> list[Path] | None:
    """Frames already chosen for this clip ([] = it was looked at and had none worth keeping), None = not yet.
    The key is the video's own id, so the three TikTok sources that return the same clip under three different signed
    URLs download it once - and a rebuild downloads nothing at all."""
    f = _choice_file(key)
    if f.exists():
        try:
            names = json.loads(f.read_text(encoding="utf-8"))
            done = [frames_dir() / n for n in names]
            if all(d.exists() for d in done):
                return done
        except (OSError, ValueError):
            pass
    old = paths_for(key, FRACTIONS)          # cut before the choice existed: the two fixed seeks
    return old if all(d.exists() for d in old) else None


def _sharpness(im: Image.Image) -> float:
    g = im.convert("L")
    g.thumbnail((200, 200))
    return float(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0])


async def choose(paths: list[Path], duration_s: float) -> list[Path]:
    """The frames worth sending on: CLIP's place-versus-junk reading (as categorize.py will read them), sharpness,
    brightness and variety. Two from a short clip, three from one over 40 s (a tour); none when every frame is a
    talking head, a caption card or the app's interface."""
    from . import vision
    ims = []
    for p in paths:
        try:
            ims.append(Image.open(p).convert("RGB"))
        except OSError:
            ims.append(None)
    ok = [i for i, im in enumerate(ims) if im is not None]
    if not ok:
        return []
    keep = 3 if duration_s >= 40 else 2
    if not vision.clip.ready:                # the model is still loading: two moments spread over the clip
        return [paths[i] for i in ok[:: max(1, len(ok) // keep)]][:keep]
    emb = await vision.encode([ims[i] for i in ok])
    clf = vision.clip.classify(emb)
    t = await asyncio.to_thread(vision.clip.texts, PLACE + VLOG)
    logits = 100.0 * emb @ t.T
    logits -= logits.max(axis=1, keepdims=True)
    prob = np.exp(logits)
    p_place = prob[:, :len(PLACE)].sum(axis=1) / prob.sum(axis=1)
    scored = []
    for j, i in enumerate(ok):
        c = clf[j]
        junk = c["junk_total"]
        light = ImageStat.Stat(ims[i].convert("L").resize((64, 64))).mean[0]
        dark = light < 40 or light > 240        # a night-black or blown-out frame shows nothing of the place
        s = float(p_place[j]) + min(0.2, _sharpness(ims[i]) / 5000)
        # what categorize.py would reject as "not a campus photo", or a frame of the person rather than the place,
        # is not worth an inspector call
        junky = (junk > 0.55 and junk > max(c["categories_raw"].values())) or p_place[j] < MIN_PLACE or dark
        scored.append((s, j, i, junky))
    chosen: list[tuple[int, int]] = []
    for s, j, i, junky in sorted(scored, reverse=True):
        if len(chosen) >= keep or junky:
            continue
        if any(float(np.dot(emb[j], emb[cj])) >= SAME_VIEW for cj, _ in chosen):
            continue
        chosen.append((j, i))
    return [paths[i] for _, i in sorted(chosen, key=lambda x: x[1])]


async def _keep(key: str, grabbed: list[Path], duration_s: float) -> list[Path]:
    """Chooses among the sampled frames, deletes the rest and remembers the choice for the next build."""
    try:
        picked = await choose(grabbed, duration_s)
    except Exception as e:  # noqa: BLE001
        log.debug("frame choice failed: %r", e)
        picked = grabbed[:: max(1, len(grabbed) // 2)][:2]
    for g in grabbed:
        if g not in picked:
            g.unlink(missing_ok=True)
    try:
        _choice_file(key).write_text(json.dumps([p.name for p in picked]), encoding="utf-8")
    except OSError:
        pass
    return picked


async def frames(url: str, duration_s: float, key: str | None = None, timeout: float = 8.0) -> list[Path]:
    """Downloads a short video and returns the chosen frames (already on disk, reused on a rebuild)."""
    if not FFMPEG or not url or duration_s <= 0:
        return []
    key = key or hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    async with _locks.setdefault(key, asyncio.Lock()):
        if (done := ready(key)) is not None:
            return done
        grabbed = await _download(url, duration_s, key, timeout)
        return await _keep(key, grabbed, duration_s) if grabbed else []


def _grab_url(url: str, duration_s: float, out_paths: list[Path], fracs=REMOTE_SAMPLES) -> list[Path]:
    """Runs in a worker thread: ffmpeg seeks inside the remote file with range requests and reads only the bytes
    around each frame. Instagram reels are often 10-40 MB; downloading them whole took up to 40 s, and the ones past
    the size cap were lost (27 of 50 in the audit of 18 Sep). TikTok's CDN refuses ffmpeg (403) - it is downloaded."""
    out: list[Path] = []
    for frac, dest in zip(fracs, out_paths):
        try:
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-headers", f"User-Agent: {UA['User-Agent']}\r\n",
                            "-ss", f"{duration_s * frac:.2f}", "-i", url, "-frames:v", "1", "-q:v", "3", str(dest)],
                           check=False, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("ffmpeg url failed: %r", e)
            continue
        if dest.exists() and dest.stat().st_size > 4000:
            out.append(dest)
    return out


async def _download(url: str, duration_s: float, key: str, timeout: float) -> list[Path]:
    if "cdninstagram" in url or "fbcdn" in url:
        return await asyncio.to_thread(_grab_url, url, min(duration_s, 600.0), paths_for(key, REMOTE_SAMPLES))
    try:
        r = await http.get(url, headers=_headers(url), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log.debug("video download failed: %r", e)
        return []
    if r.status_code != 200 or not (2000 < len(r.content) <= MAX_BYTES):
        return []
    tmp = Path(tempfile.gettempdir()) / f"cl_{key}.mp4"
    try:
        tmp.write_bytes(r.content)
        return await asyncio.to_thread(_grab, tmp, min(duration_s, 600.0), paths_for(key))
    finally:
        tmp.unlink(missing_ok=True)


async def many(videos: list[tuple[str, float, str]], concurrency: int = 4) -> list[list[Path]]:
    """Frames for several videos at once; a video that fails contributes an empty list, not an exception."""
    sem = asyncio.Semaphore(concurrency)

    async def one(v: tuple[str, float, str]) -> list[Path]:
        async with sem:
            try:
                return await frames(*v)
            except Exception as e:  # noqa: BLE001
                log.debug("frames failed: %r", e)
                return []

    # clips still downloading when the source is due are dropped; the ones already cut are kept
    return [r or [] for r in await until_deadline([one(v) for v in videos], margin=1.0)]
