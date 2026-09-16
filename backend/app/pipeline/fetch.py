"""Download candidate images, reject tiny/odd ones, extract EXIF, compute pHash, store a thumbnail."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from dataclasses import dataclass, field

import imagehash
from PIL import Image, ImageOps

from .. import http
from ..config import settings
from ..models import PhotoCandidate

log = logging.getLogger("campuslens.fetch")
Image.MAX_IMAGE_PIXELS = 60_000_000

MIN_SIDE = 300
MAX_BYTES = 12_000_000


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


def _decode(data: bytes, pid: str) -> dict | None:
    """Runs in a worker thread."""
    try:
        im = Image.open(io.BytesIO(data))
        fmt = (im.format or "").upper()
        if fmt in ("GIF", "SVG", "ICO", "BMP"):
            return None
        w, h = im.size
        if min(w, h) < MIN_SIDE or w / h > 3.5 or h / w > 3.5:
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
        try:
            r = await http.get(cand.url, timeout=settings.fetch_timeout_s)
        except Exception:
            return None
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        data = r.content
        if len(data) > MAX_BYTES or len(data) < 2000:
            return None
        looks_like_image = data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG" or data[:4] == b"RIFF"
        if not ct.startswith("image/") and not looks_like_image:
            return None
        dec = await asyncio.to_thread(_decode, data, pid)
        if not dec:
            return None
        return Fetched(cand=cand, id=pid, last_modified=r.headers.get("last-modified"), **dec)


async def fetch_all(cands: list[PhotoCandidate], deadline: float, limit: int | None = None) -> list[Fetched]:
    sem = asyncio.Semaphore(settings.fetch_concurrency)
    seen: set[str] = set()
    uniq: list[PhotoCandidate] = []
    for c in cands:
        if c.url not in seen:
            seen.add(c.url)
            uniq.append(c)
    if limit:
        uniq = uniq[:limit]
    results = await asyncio.gather(*[fetch_one(c, sem, deadline) for c in uniq], return_exceptions=True)
    return [r for r in results if isinstance(r, Fetched)]
