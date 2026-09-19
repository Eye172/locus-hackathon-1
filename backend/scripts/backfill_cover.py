"""Pick the cover (pipeline/cover.py) for saved profiles and store it in them.

    python scripts/backfill_cover.py                 # every saved profile without a cover
    python scripts/backfill_cover.py Q2783344 Q7842  # these, again
    python scripts/backfill_cover.py --all --force   # every profile, asking the editor again
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app import cache, http  # noqa: E402
from app.pipeline import cover  # noqa: E402


async def one(qid: str, force: bool, sem: asyncio.Semaphore) -> str:
    try:
        return await _one(qid, force, sem)
    except Exception as e:  # noqa: BLE001  (a busy database, a broken profile: the others go on)
        return f"{qid}: failed {e!r}"


async def _one(qid: str, force: bool, sem: asyncio.Semaphore) -> str:
    async with sem:
        p = await cache.get_profile(qid)
        if not p:
            return f"{qid}: not saved"
        cands = cover.candidates(p.photos)
        k = cover.key(qid, cands)
        if not k:
            return f"{qid}: {len(cands)} candidates, too few"
        res = None if force else await cover.cached(k)
        res = res or await cover.pick(p.university, cands, k)
        if not res:
            return f"{qid}: editor failed"
        p.cover = cover.apply(res, p.photos)
        for attempt in range(5):     # the running server writes to the same file
            try:
                await cache.save_profile(p)
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.0 + attempt)
        return f"{qid}: {len(cands)} candidates -> {len(p.cover)} ({res.model})"


async def main() -> None:
    await cache.init()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if args:
        qids = args
    else:
        import sqlite3
        from app.config import settings
        con = sqlite3.connect(settings.data_dir / "campuslens.sqlite3")
        qids = [q for (q, j) in con.execute("select qid, json from profiles")
                if "--all" in sys.argv or '"cover": [' not in j or '"cover": []' in j]
    sem = asyncio.Semaphore(4)
    for f in asyncio.as_completed([one(q, force, sem) for q in qids]):
        print(await f, flush=True)
    await http.close()


asyncio.run(main())
