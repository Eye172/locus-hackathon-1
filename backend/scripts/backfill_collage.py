"""Adds the collage ("what is this university like") and the search-plan summary to every saved profile that was
built before they existed. Offline: no API is called, only the photos already in each profile are sorted into themes.
A profile built by the new pipeline already has both and is left as it is (unless --force).

    python scripts/backfill_collage.py [--force]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import collage, search_plan  # noqa: E402
from app.pipeline.resolve import index  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    await cache.init()
    index.load()
    import aiosqlite  # noqa: F401  (cache uses it; the list comes from the same database)
    rows = await cache.list_recent(10_000)
    done = skipped = 0
    for r in rows:
        p = await cache.get_profile(r["qid"])
        if p is None:
            continue
        if p.collage and any(s.photos for s in p.collage) and p.plan and not a.force:
            skipped += 1
            continue
        plan = await search_plan.for_university(p.university.qid)
        p.collage = collage.build(p.photos, plan)
        p.plan = collage.plan_summary(plan, p.university)
        await cache.save_profile(p)
        done += 1
        n = sum(len(s.photos) for s in p.collage)
        print(f"{p.university.qid:12} {(p.university.names.get('en') or p.university.name)[:40]:40} "
              f"collage {n:3} in {sum(1 for s in p.collage if s.photos)} themes")
    print(f"updated {done}, already had a collage {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
