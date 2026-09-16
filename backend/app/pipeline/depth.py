"""Monocular depth maps (Depth Anything V2 Small, CPU) for the honest "3D photo" parallax effect.

The photo itself is never modified: the client only shifts pixels slightly by depth while the cursor moves.
Maps are generated lazily on first request and cached as 8-bit PNG (bright = near).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PIL import Image

from ..config import settings

log = logging.getLogger("campuslens.depth")
MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
_pipe = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()


def _load():
    global _pipe
    with _load_lock:
        if _pipe is None:
            from transformers import pipeline
            _pipe = pipeline("depth-estimation", model=MODEL, device=-1)
            log.info("depth model ready")
    return _pipe


def _run(src: Path, dst: Path) -> None:
    pipe = _load()
    im = Image.open(src).convert("RGB")
    im.thumbnail((640, 640))
    with _infer_lock:
        depth = pipe(im)["depth"]
    depth = depth.convert("L").resize(im.size)
    dst.parent.mkdir(parents=True, exist_ok=True)
    depth.save(dst, "PNG", optimize=True)


def depth_path(photo_id: str) -> Path:
    return settings.data_dir / "depth" / f"{photo_id}.png"


async def ensure(photo_id: str) -> Path | None:
    dst = depth_path(photo_id)
    if dst.exists():
        return dst
    src = settings.thumbs_dir / f"{photo_id}.jpg"
    if not src.exists():
        return None
    await asyncio.to_thread(_run, src, dst)
    return dst


async def warmup() -> None:
    try:
        await asyncio.to_thread(_load)
    except Exception as e:  # noqa: BLE001
        log.warning("depth warmup failed: %s", e)
