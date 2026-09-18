"""Frames from the middle of a short video, instead of the cover someone designed for it.

A TikTok cover is a poster: the title in big letters, a face, an arrow. The AI inspector rejects it, and rightly so.
Yet the video behind that cover is often the only footage of the campus that exists anywhere - the atrium with its
palm trees, a corridor between classes, the queue at the canteen - filmed by a student with no reason to stage it.
Seeking to a third and two thirds of the clip gets that footage for the price of one small download.

The frames are written to `data/frames` and handed to the pipeline as `file:` candidates, so they go through the
same funnel as every other photo: hashing, de-duplication, the inspector, the confidence score.

Without ffmpeg on the machine this module quietly returns nothing and the sources fall back to covers.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import http
from ..config import settings

log = logging.getLogger("campuslens.frames")

FFMPEG = shutil.which("ffmpeg")
FRACTIONS = (0.35, 0.7)     # a third in, two thirds in: past the intro, before the call to subscribe
MAX_BYTES = 8_000_000       # a 60 s vertical clip is ~3 MB; anything larger is not worth the budget
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


def _grab(video: Path, duration_s: float, out_paths: list[Path]) -> list[Path]:
    """Runs in a worker thread: one ffmpeg seek per frame (fast, the file is already local)."""
    out: list[Path] = []
    for frac, dest in zip(FRACTIONS, out_paths):
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


def paths_for(key: str) -> list[Path]:
    return [frames_dir() / f"{key}_{int(f * 100)}.jpg" for f in FRACTIONS]


def ready(key: str) -> list[Path]:
    """Frames already on disk. The key is the video's own id, so the three TikTok sources that return the same
    clip under three different signed URLs download it once - and a rebuild downloads nothing at all."""
    done = [d for d in paths_for(key) if d.exists()]
    return done if len(done) == len(FRACTIONS) else []


async def frames(url: str, duration_s: float, key: str | None = None, timeout: float = 8.0) -> list[Path]:
    """Downloads a short video and returns the extracted frames (already on disk, reused on a rebuild)."""
    if not FFMPEG or not url or duration_s <= 0:
        return []
    key = key or hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    dests = paths_for(key)
    async with _locks.setdefault(key, asyncio.Lock()):
        if (done := ready(key)):
            return done
        return await _download(url, duration_s, dests, key, timeout)


async def _download(url: str, duration_s: float, dests: list[Path], key: str, timeout: float) -> list[Path]:
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
        return await asyncio.to_thread(_grab, tmp, min(duration_s, 600.0), dests)
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

    return await asyncio.gather(*[one(v) for v in videos])
