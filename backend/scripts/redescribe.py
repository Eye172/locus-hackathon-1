"""Write the description of saved profiles again with today's describe.py (names as app/pipeline/names.py shows
them: «Stanford University — частный…», not «Стэ́нфордский университе́т — …»). A template description is rebuilt
instantly; an LLM one asks the model again (one call per profile) and becomes the template if the model fails.

    python scripts/redescribe.py [--all] [--dry]     (default: universities shown by a Latin name)
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.models import CATEGORIES, Profile  # noqa: E402
from app.pipeline import describe  # noqa: E402


def stats_of(p: Profile) -> dict:
    """The facts the orchestrator hands to describe.build(), from the saved profile."""
    return {"verified": sum(s.verified for s in p.categories.values()),
            "likely": sum(s.likely for s in p.categories.values()),
            "sources": len({s for ph in p.photos for s in ph.sources}),
            "weak": [c for c in CATEGORIES if p.coverage.get(c) in ("weak", "none") and c != "city"],
            "per_category": {c: {"verified": s.verified, "likely": s.likely} for c, s in p.categories.items()}}


async def main(everyone: bool, dry: bool) -> None:
    db = sqlite3.connect(cache.DB_PATH)
    rows = db.execute("SELECT qid, json FROM profiles").fetchall()
    sem = asyncio.Semaphore(6)
    done = 0

    async def one(qid: str, raw: str) -> None:
        nonlocal done
        p = Profile.model_validate_json(raw)
        if not everyone and re.search(r"[А-Яа-яЁё]", p.university.name):
            return
        old = p.description
        ctx = p.context.model_dump() if p.context else None
        async with sem:
            if old is not None and old.mode == "llm":  # a reply that is not Russian prose falls back to the template
                d = await describe.build(p.university, p.campus, stats_of(p), ctx, timeout=25.0)
            else:
                d = describe.template(p.university, p.campus, stats_of(p), ctx)
        print(f"{qid} {p.university.name} [{d.mode}]: {d.sentences[0].text[:110] if d.sentences else ''}")
        if dry:
            return
        j = json.loads(raw)
        j["description"] = json.loads(d.model_dump_json())
        db.execute("UPDATE profiles SET json=? WHERE qid=?", (json.dumps(j, ensure_ascii=False), qid))
        db.commit()
        done += 1

    await asyncio.gather(*[one(q, r) for q, r in rows])
    print(f"{done} descriptions written")


if __name__ == "__main__":
    asyncio.run(main("--all" in sys.argv, "--dry" in sys.argv))
