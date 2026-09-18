"""Recomputes the collage of a saved profile with the current code and plan (no rebuild), prints it, draws a sheet.

    python scripts/recollage.py Q2783344 [--sheet out.jpg] [--save]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline import collage, search_plan  # noqa: E402


def sheet(prof, path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    T, COLS = 200, 10
    by_id = {p.id: p for p in prof.photos}
    rows = sum((len(s.photos) + COLS - 1) // COLS for s in prof.collage) + len(prof.collage)
    img = Image.new("RGB", (COLS * T, rows * (T + 30)), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 20)
    small = ImageFont.truetype("arial.ttf", 12)
    y = 0
    for sec in prof.collage:
        d.text((6, y + 8), f"{sec.label}: {len(sec.photos)}/{sec.target}", fill=(0, 0, 0), font=font)
        y += 40
        for k, pid in enumerate(sec.photos):
            if k and k % COLS == 0:
                y += T + 18
            p = by_id[pid]
            f = settings.thumbs_dir / f"{pid}.jpg"
            if f.exists():
                im = Image.open(f)
                im.thumbnail((T - 4, T - 4))
                img.paste(im, ((k % COLS) * T + (T - im.width) // 2, y))
            d.text(((k % COLS) * T + 3, y + T - 2), p.source[:22], fill=(90, 90, 90), font=small)
        y += T + 18
    img.crop((0, 0, COLS * T, y)).save(path, quality=82)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--sheet")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    await cache.init()
    prof = await cache.get_profile(a.qid)
    plan = await search_plan.for_university(a.qid)
    prof.collage = collage.build(prof.photos, plan)
    by_id = {p.id: p for p in prof.photos}
    for sec in prof.collage:
        srcs = {}
        for pid in sec.photos:
            srcs[by_id[pid].source] = srcs.get(by_id[pid].source, 0) + 1
        print(f"{sec.label:28} {len(sec.photos):2}/{sec.target:<2} fits {sec.found:3}  {srcs}")
    if a.sheet:
        sheet(prof, a.sheet)
    if a.save:
        await cache.save_profile(prof)


if __name__ == "__main__":
    asyncio.run(main())
