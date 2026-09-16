"""End-to-end smoke test without the HTTP layer.

    python scripts/smoke.py Q427677            # run the pipeline for a QID
    python scripts/smoke.py --search "КазНУ"   # test name resolution only
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import orchestrator  # noqa: E402
from app.pipeline.resolve import index, resolve  # noqa: E402


async def main() -> None:
    await cache.init()
    index.load()
    if sys.argv[1] == "--search":
        for q in sys.argv[2:]:
            t = time.time()
            cands = await resolve(q)
            print(f"\n'{q}' ({(time.time()-t)*1000:.0f} ms)")
            for c in cands[:6]:
                print(f"  {c.qid:>12} {c.score:.2f} {c.origin:8} {c.label} | {c.city or ''} {c.country or ''}")
        return
    qid = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "--refresh":
        await cache.delete_profile(qid)

    async def emit(ev: dict) -> None:
        t = ev.get("elapsed_ms", 0)
        if ev["type"] == "stage":
            s = ev["stage"]
            print(f"[{t:>6}] stage {s['key']:<9} {s['status']:<8} {s.get('detail') or ''} {('n=' + str(s['count'])) if s.get('count') is not None else ''}")
        elif ev["type"] == "source":
            print(f"[{t:>6}] source {ev['name']:<16} {ev['status']:<9} n={ev.get('count', 0)} {ev.get('detail') or ''}")
        elif ev["type"] == "photos":
            print(f"[{t:>6}] photos {ev['source']:<16} +{len(ev['photos'])} prelim, rejected {ev['rejected']}")
        elif ev["type"] == "university":
            u = ev["university"]
            print(f"[{t:>6}] university {u['name']} | {u['city']} | coords {u['lat']:.4f},{u['lon']:.4f} ({u['coord_source']}) | site {u['website']} | commons {u['commons_category']}")
        elif ev["type"] == "profile":
            p = ev["profile"]
            print(f"[{t:>6}] PROFILE photos={len(p['photos'])} rejected={len(p['rejected'])} partial={p['partial']} elapsed={p['elapsed_ms']} ms")
            print("  coverage:", p["coverage"])
            print("  categories:", {c: (s["verified"], s["likely"], s["rejected"]) for c, s in p["categories"].items()})
            print("  context:", p["context"])
            print("  description:", p["description"]["mode"])
            for s in p["description"]["sentences"]:
                print("    -", s["text"], s["sources"])
            print("  top photos:")
            for ph in p["photos"][:12]:
                print(f"    {ph['confidence']:.2f} {ph['level']:<9} {ph['category']:<12} {ph['source']:<15} sources={ph['sources_count']} geo={ph['geo_inside']} date={ph['date']} | {(ph['title'] or '')[:50]} | similar={len(ph['similar'])}")
            print("  rejected sample:")
            for ph in p["rejected"][:8]:
                print(f"    {ph['confidence']:.2f} {ph['source']:<15} {ph['reject_reason']} | {(ph['title'] or '')[:40]}")
            Path("last_profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        elif ev["type"] == "error":
            print(f"[{t:>6}] ERROR {ev['message']}")
            for line in ev.get("log", []):
                print("   ", line)

    await orchestrator.run(qid, emit)


if __name__ == "__main__":
    asyncio.run(main())
