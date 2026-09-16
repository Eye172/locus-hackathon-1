"""Pre-build profiles (and climate/context packs) for a list of universities so the demo opens instantly.

    python scripts/prewarm.py                 # data/prewarm_list.json
    python scripts/prewarm.py Q427677 Q2783344
    python scripts/prewarm.py --kz            # every Kazakhstani university in the index

Same pipeline as a live request; nothing is hard-coded. Profiles are stamped with generated_at and can be
rebuilt from the UI. Runs 2 builds in parallel to stay polite to public APIs.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import city, climate, orchestrator  # noqa: E402
from app.pipeline.resolve import index  # noqa: E402

LIST = Path(__file__).resolve().parents[1] / "data" / "prewarm_list.json"


async def one(qid: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        t = time.time()
        try:
            if await cache.get_profile(qid):
                print(f"{qid}: cached, skip")
            else:
                async def emit(_: dict) -> None:
                    return None
                p = await orchestrator.run(qid, emit)
                print(f"{qid}: {p.university.name} -> {len(p.photos)} photos, {len(p.rejected)} rejected, {p.elapsed_ms} ms")
            p = await cache.get_profile(qid)
            if p and p.university.lat is not None:
                if not await cache.kv_get("climate", qid, max_age_s=30 * 86400):
                    await cache.kv_set("climate", qid, await climate.build(p.university.lat, p.university.lon, p.university.city))
                if not await cache.kv_get("context", qid, max_age_s=7 * 86400):
                    pack = await city.build(p.university, p.campus)
                    if pack.get("poi") or pack.get("campus_buildings", {}).get("features"):
                        await cache.kv_set("context", qid, pack)
                    else:
                        print(f"{qid}: city pack empty (Overpass down?), not cached")
            print(f"{qid}: done in {time.time() - t:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"{qid}: FAILED {type(e).__name__}: {e}")


async def main() -> None:
    await cache.init()
    index.load()
    args = sys.argv[1:]
    if args == ["--kz"]:
        qids = [r["id"] for r in index.rows if r["country"] == "Q232"]
    elif args:
        qids = args
    else:
        qids = json.loads(LIST.read_text(encoding="utf-8")) if LIST.exists() else []
    print(f"prewarming {len(qids)} universities")
    sem = asyncio.Semaphore(2)
    await asyncio.gather(*[one(q, sem) for q in qids])


if __name__ == "__main__":
    asyncio.run(main())
