"""Builds one profile in-process (the same run the API starts), prints the timeline, the sources and the collage.

    python scripts/build_once.py Q2783344 [--sheet out.jpg]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache, http  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline import orchestrator, vision  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--sheet")
    a = ap.parse_args()
    await cache.init()
    from app.pipeline.resolve import index
    index.load()
    await vision.warmup()
    t0 = time.monotonic()

    async def emit(ev: dict) -> None:
        if ev["type"] == "profile":
            p = ev["profile"]
            print(f"[{time.monotonic() - t0:6.1f}s] profile final={ev.get('final')} photos={len(p['photos'])} "
                  f"collage={[(s['key'], len(s['photos'])) for s in p.get('collage') or []]}", flush=True)
        elif ev["type"] == "source" and ev.get("status") in ("done", "error", "skipped"):
            print(f"[{time.monotonic() - t0:6.1f}s] {ev['name']:18} {ev['status']:7} {ev.get('count')} {ev.get('detail') or ''}",
                  flush=True)
        elif ev["type"] == "error":
            print("ERROR", ev.get("message"), flush=True)

    prof = await orchestrator.run(a.qid, emit)
    by_id = {p.id: p for p in prof.photos}
    for sec in prof.collage:
        print(f"\n== {sec.label} ({len(sec.photos)}/{sec.target}, fits {sec.found})")
        for pid in sec.photos:
            p = by_id[pid]
            print(f"   {p.source:18} {p.intent or '-':11} {p.category:12} q={p.quality} {p.level[:3]} "
                  f"{(p.query or '')[:40]!r} {(p.title or '')[:50]!r}")
    if a.sheet:
        from PIL import Image, ImageDraw, ImageFont
        T, COLS = 200, 10
        rows = sum((len(s.photos) + COLS - 1) // COLS for s in prof.collage) + len(prof.collage)
        sheet = Image.new("RGB", (COLS * T, rows * (T + 4)), "white")
        d = ImageDraw.Draw(sheet)
        font = ImageFont.truetype("arial.ttf", 18)
        y = 0
        for sec in prof.collage:
            d.text((6, y + T // 2 - 10), f"{sec.label}: {len(sec.photos)}/{sec.target}", fill=(0, 0, 0), font=font)
            y += T // 2 + 4
            for k, pid in enumerate(sec.photos):
                path = settings.thumbs_dir / f"{pid}.jpg"
                if not path.exists():
                    continue
                im = Image.open(path)
                im.thumbnail((T - 4, T - 4))
                x = (k % COLS) * T
                if k and k % COLS == 0:
                    y += T + 4
                sheet.paste(im, (x + (T - im.width) // 2, y))
            y += T + 4
        sheet.crop((0, 0, COLS * T, y)).save(a.sheet, quality=82)
        print("sheet", a.sheet)
    await http.close()


if __name__ == "__main__":
    asyncio.run(main())
