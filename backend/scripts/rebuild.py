"""Rebuild cached profiles with the current pipeline and print a one-line summary per university.

    python scripts/rebuild.py Q1734762 Q427677      # rebuild these (the cached profile is replaced)
    python scripts/rebuild.py --labelled            # every university in data/labels.json

CLIP is loaded before the first build, so model start-up never eats the time budget.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline import orchestrator, vision  # noqa: E402
from app.pipeline.resolve import index  # noqa: E402


async def main() -> None:
    await cache.init()
    index.load()
    qids = sys.argv[1:]
    if qids == ["--labelled"]:
        labels = json.loads((settings.data_dir / "labels.json").read_text(encoding="utf-8"))
        qids = [k for k in labels if not k.startswith("_")]
    t = time.time()
    await vision.warmup()
    print(f"CLIP ready in {time.time() - t:.1f} s")

    async def emit(_: dict) -> None:
        return None

    for qid in qids:
        try:
            p = await orchestrator.run(qid, emit)
        except Exception as e:  # noqa: BLE001
            print(f"{qid}: FAILED {e!r}")
            continue
        st = {s.key: s.ms for s in p.stages}
        ins = p.inspector or {}
        feat = [x for x in p.photos if x.featured]
        print(f"{qid} {p.university.name[:40]:<40} {p.elapsed_ms / 1000:5.1f} s | kept {len(p.photos):3} "
              f"featured {len(feat):3} rejected {len(p.rejected):3} | AI {ins.get('photos', 0)}/{ins.get('submitted', 0)} "
              f"calls {ins.get('calls', 0)} tok {ins.get('tokens_in', 0)}+{ins.get('tokens_out', 0)} err {ins.get('errors', 0)} "
              f"ref {'y' if p.reference else 'n'} | stages {st}")
        print("   featured by category:", dict(Counter(x.category for x in feat)))
        print("   reject reasons:", dict(Counter((x.reject_reason or '').split(' · ')[0].split(' (')[0] for x in p.rejected).most_common(6)))


if __name__ == "__main__":
    asyncio.run(main())
