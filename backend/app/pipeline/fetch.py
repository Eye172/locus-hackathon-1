"""Download candidate images, reject tiny/odd ones, extract EXIF, compute pHash, store a thumbnail."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
from PIL import Image, ImageOps

from .. import http
from ..config import settings
from ..models import PhotoCandidate

log = logging.getLogger("campuslens.fetch")
Image.MAX_IMAGE_PIXELS = 60_000_000

MIN_SIDE = 300
MAX_BYTES = 12_000_000
# Wikimedia panoramas are legitimate wide shots; on websites and social feeds a 3:1 image is a slider strip or banner
WIDE_OK_SOURCES = {"commons_cat", "commons_depicts", "commons_geo", "wikipedia", "city_article", "city_cat"}


@dataclass
class Fetched:
    cand: PhotoCandidate
    id: str
    image: Image.Image
    width: int
    height: int
    phash: str
    dhash: str = ""
    sha1: str = ""
    exif_date: str | None = None
    exif_lat: float | None = None
    exif_lon: float | None = None
    last_modified: str | None = None
    extra_sources: list[PhotoCandidate] = field(default_factory=list)


def photo_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _dms_to_deg(v, ref) -> float | None:
    try:
        d, m, s = [float(x) for x in v]
        deg = d + m / 60 + s / 3600
        return -deg if ref in ("S", "W") else deg
    except Exception:
        return None


def _decode(data: bytes, pid: str, max_aspect: float = 3.5) -> dict | None:
    """Runs in a worker thread."""
    try:
        im = Image.open(io.BytesIO(data))
        fmt = (im.format or "").upper()
        if fmt in ("GIF", "SVG", "ICO", "BMP"):
            return None
        w, h = im.size
        if min(w, h) < MIN_SIDE or w / h > max_aspect or h / w > 3.5:
            return None
        exif_date = exif_lat = exif_lon = None
        try:
            exif = im.getexif()
            ifd = exif.get_ifd(0x8769)
            raw = ifd.get(36867) or exif.get(306)
            if raw and len(str(raw)) >= 10:
                s = str(raw)[:10].replace(":", "-")
                if s[:4].isdigit() and s[:4] != "0000":
                    exif_date = s
            gps = exif.get_ifd(0x8825)
            if gps and 2 in gps and 4 in gps:
                exif_lat = _dms_to_deg(gps[2], gps.get(1, "N"))
                exif_lon = _dms_to_deg(gps[4], gps.get(3, "E"))
        except Exception:
            pass
        im = ImageOps.exif_transpose(im).convert("RGB")
        ph = str(imagehash.phash(im))
        dh = str(imagehash.dhash(im))          # 8x8 dHash, same as the classic Sharp/Node implementation
        sha1 = hashlib.sha1(data).hexdigest()
        # near-uniform images (blank placeholders) are junk
        small = im.resize((32, 32))
        px = list(small.getdata())
        mean = [sum(c[i] for c in px) / len(px) for i in range(3)]
        var = sum(sum((c[i] - mean[i]) ** 2 for i in range(3)) for c in px) / len(px)
        if var < 60:
            return None
        im.thumbnail((640, 640))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        (settings.thumbs_dir / f"{pid}.jpg").write_bytes(buf.getvalue())
        return {"image": im, "width": w, "height": h, "phash": ph, "dhash": dh, "sha1": sha1,
                "exif_date": exif_date, "exif_lat": exif_lat, "exif_lon": exif_lon}
    except Exception as e:  # noqa: BLE001
        log.debug("decode failed %s: %s", pid, e)
        return None


async def fetch_one(cand: PhotoCandidate, sem: asyncio.Semaphore, deadline: float) -> Fetched | None:
    if time.monotonic() > deadline:
        return None
    pid = photo_id(cand.url)
    async with sem:
        if time.monotonic() > deadline:
            return None
        last_modified = None
        if cand.url.startswith("file:"):
            # a frame this pipeline already extracted from a video (pipeline/video_frames.py)
            try:
                data, ct = Path(cand.url[5:]).read_bytes(), "image/jpeg"
            except OSError:
                return None
        else:
            try:
                r = await http.get(cand.url, timeout=settings.fetch_timeout_s)
            except Exception:
                return None
            if r.status_code != 200:
                return None
            ct, data, last_modified = r.headers.get("content-type", ""), r.content, r.headers.get("last-modified")
        if len(data) > MAX_BYTES or len(data) < 2000:
            return None
        looks_like_image = data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG" or data[:4] == b"RIFF"
        if not ct.startswith("image/") and not looks_like_image:
            return None
        dec = await asyncio.to_thread(_decode, data, pid, 3.5 if cand.source in WIDE_OK_SOURCES else 2.4)
        if not dec:
            return None
        return Fetched(cand=cand, id=pid, last_modified=last_modified, **dec)


async def fetch_all(cands: list[PhotoCandidate], deadline: float, limit: int | None = None,
                    late: list[asyncio.Task] | None = None) -> list[Fetched]:
    """What has downloaded by `deadline` (math.inf = everything). With `late`, the downloads still running at the
    deadline are not dropped: they are handed over there to finish (each task resolves to a Fetched or None)."""
    sem = asyncio.Semaphore(settings.fetch_concurrency)
    seen: set[str] = set()
    uniq: list[PhotoCandidate] = []
    for c in cands:
        if c.url not in seen:
            seen.add(c.url)
            uniq.append(c)
    if limit:
        uniq = uniq[:limit]
    if not uniq:
        return []
    # stop at the deadline and keep whatever has arrived: a slow source still contributes its first images
    tasks = [asyncio.create_task(fetch_one(c, sem, math.inf if late is not None else deadline)) for c in uniq]
    done, pending = await asyncio.wait(tasks, timeout=None if deadline == math.inf
                                       else max(0.1, deadline - time.monotonic()))
    if late is not None:
        late.extend(pending)
    else:
        for t in pending:
            t.cancel()
    return [r for t in done if not t.cancelled() and t.exception() is None and isinstance(r := t.result(), Fetched)]


async def fetch_waves(cands: list[PhotoCandidate], deadline: float, on_batch, limit: int | None = None,
                      late: list[asyncio.Task] | None = None, wave_s: float = 1.5) -> int:
    """Like fetch_all, but hands over what has downloaded every `wave_s` seconds instead of waiting for the slowest
    image of the source. The embedding and the AI inspector start on the first images while the rest still arrive:
    waiting for the slowest one made every source hand its images over at the first profile's collect deadline, and
    the inspector had no time left to look at any of them (the first profile of KBTU on 18 Sep: 0 calls in 23 s).
    Returns how many images were handed over; with `late`, the downloads still running at the deadline go there."""
    sem = asyncio.Semaphore(settings.fetch_concurrency)
    seen: set[str] = set()
    uniq: list[PhotoCandidate] = []
    for c in cands:
        if c.url not in seen:
            seen.add(c.url)
            uniq.append(c)
    if limit:
        uniq = uniq[:limit]
    pending = {asyncio.create_task(fetch_one(c, sem, math.inf if late is not None else deadline)) for c in uniq}
    handed = 0
    while pending:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        done, pending = await asyncio.wait(pending, timeout=min(wave_s, left))
        batch = [r for t in done if not t.cancelled() and t.exception() is None and isinstance(r := t.result(), Fetched)]
        if batch:
            handed += len(batch)
            await on_batch(batch)
    if pending:
        if late is not None:
            late.extend(pending)
        else:
            for t in pending:
                t.cancel()
    return handed
