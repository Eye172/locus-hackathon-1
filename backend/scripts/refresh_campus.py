"""Re-fetch OSM campus data (polygon + tagged buildings) and city packs for cached profiles that were built while
Overpass was unreachable. Photos and confidence scores are left untouched.

    python scripts/refresh_campus.py            # every cached profile with no buildings / empty city pack
    python scripts/refresh_campus.py Q2783344   # specific QIDs
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import city, enrich  # noqa: E402
from app.pipeline.resolve import index  # noqa: E402


async def one(qid: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        p = await cache.get_profile(qid)
        if not p:
            print(f"{qid}: no profile"); return
        t = time.time()
        try:
            if not p.campus or not p.campus.buildings:
                c = await enrich.campus(p.university)
                better = c and (c.buildings or (c.mode == "polygon" and (not p.campus or p.campus.mode != "polygon")))
                if better:
                    p.campus = c
                    await cache.save_profile(p)
                print(f"{qid}: {p.university.name[:36]} campus {c.mode if c else None}, buildings {len(c.buildings) if c else 0} {'(saved)' if better else '(kept)'}")
            ctx = await cache.kv_get("context", qid)
            if not ctx or not ctx.get("campus_buildings", {}).get("features") or not ctx.get("poi"):
                new = await city.build(p.university, p.campus)
                n_b, n_p = len(new.get("campus_buildings", {}).get("features", [])), len(new.get("poi", {}))
                old_b = len((ctx or {}).get("campus_buildings", {}).get("features", []))
                old_p = len((ctx or {}).get("poi", {}))
                if n_b + n_p > old_b + old_p:   # never replace a pack with a worse one
                    await cache.kv_set("context", qid, new)
                    print(f"{qid}: city pack rebuilt: {n_b} buildings, {n_p} poi groups")
                else:
                    print(f"{qid}: city pack unchanged (Overpass returned {n_b}/{n_p})")
            print(f"{qid}: done in {time.time() - t:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"{qid}: FAILED {type(e).__name__}: {e}")


async def main() -> None:
    await cache.init()
    index.load()
    qids = [a for a in sys.argv[1:] if a.startswith("Q")]
    if not qids:
        con = sqlite3.connect(str(cache.DB_PATH))
        qids = [r[0] for r in con.execute("SELECT qid FROM profiles")]
    print(f"refreshing {len(qids)} profiles")
    sem = asyncio.Semaphore(2)
    await asyncio.gather(*[one(q, sem) for q in qids])


if __name__ == "__main__":
    asyncio.run(main())
