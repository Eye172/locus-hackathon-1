"""Give saved data the display names of app/pipeline/names.py (Russian in Russia, English
elsewhere) without rebuilding anything:
- profiles: university.name / name_en / country_qid, the profiles.name column, and the description sentences that
  were written with the old name («Стэнфордский университет основан в 1885 году» -> «Stanford University основан…»);
- cached 3D-map packs (kv 'map3d'): university.name / name_en.

    python scripts/backfill_names.py [--dry]
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import names  # noqa: E402
from app.pipeline.resolve import index  # noqa: E402
from app.pipeline.sources import wikidata  # noqa: E402


def _replace(o, old: list[str], new: str):
    """Every string inside `o` with the old names swapped for the new one."""
    if isinstance(o, str):
        for x in old:
            o = o.replace(x, new)
        return o
    if isinstance(o, list):
        return [_replace(v, old, new) for v in o]
    if isinstance(o, dict):
        return {k: _replace(v, old, new) for k, v in o.items()}
    return o


async def main(dry: bool) -> None:
    index.load()
    db = sqlite3.connect(cache.DB_PATH)
    rows = db.execute("SELECT qid, json FROM profiles").fetchall()
    shown: dict[str, tuple[str, str | None]] = {}
    changed = 0
    for qid, raw in rows:
        p = json.loads(raw)
        u = p["university"]
        labels = u.get("names") or {}
        if qid.startswith("W"):
            ent = await cache.kv_get("web", qid) or {}
            src = ent.get("name") or labels.get("en") or u["name"]
            en = ent.get("name_en") or await names.english(src, (), ent.get("country"))
            name, country = names.web_display(src, en, ent.get("country")), None
            old = {src, u["name"]} - {name}
        else:
            row = index.by_id.get(qid) or {}
            country = row.get("country") or u.get("country_qid")
            if not country:  # a university outside the index: its country from Wikidata
                claims = ((await wikidata.get_entities([qid], props="claims")).get(qid) or {}).get("claims", {})
                c = wikidata._first(claims, "P17")
                country = c.get("id") if isinstance(c, dict) else None
            en = (names.english_alternative(labels.get("en"), [(u.get("wikipedia") or {}).get("en"), *(u.get("aliases") or [])])
                  or row.get("name_en")
                  or await names.english(labels.get("en") or u["name"], (), u.get("country")))
            name = names.display(labels.get("ru"), en, u["name"], country) or u["name"]
            # what the old rule showed: the Russian label
            old = {x for x in (labels.get("ru"), u["name"]) if x and len(x) >= 6} - {name}
        shown[qid] = (name, en)
        # the longer old name first; nothing is swapped when an old name sits inside the new one
        old_list = sorted(old, key=len, reverse=True) if not any(x in name for x in old) else []
        desc = _replace(p.get("description"), old_list, name) if old_list else p.get("description")
        if (u["name"], u.get("name_en"), u.get("country_qid"), desc) == (name, en, country, p.get("description")):
            continue
        changed += 1
        if u["name"] != name or desc != p.get("description"):
            print(f"{qid}: {u['name']!r} -> {name!r}{' (+ description)' if desc != p.get('description') else ''}")
        if not dry:
            u["name"], u["name_en"], u["country_qid"] = name, en, country
            p["description"] = desc
            db.execute("UPDATE profiles SET json=?, name=? WHERE qid=?", (json.dumps(p, ensure_ascii=False), name, qid))
            db.commit()
    print(f"{changed} of {len(rows)} profiles {'would change' if dry else 'updated'}")

    packs = db.execute("SELECT key, json FROM kv_cache WHERE bucket='map3d'").fetchall()
    fixed = 0
    for key, raw in packs:
        qid = key.split(":")[0]
        row = index.by_id.get(qid)
        new = shown.get(qid) or ((names.row_name(row), names.row_en(row)) if row else None)
        pack = json.loads(raw)
        u = pack.get("university") or {}
        if not new or (u.get("name"), u.get("name_en")) == new:
            continue
        fixed += 1
        u["name"], u["name_en"] = new
        if not dry:  # created_at stays: the pack keeps its age
            db.execute("UPDATE kv_cache SET json=? WHERE bucket='map3d' AND key=?", (json.dumps(pack, ensure_ascii=False), key))
            db.commit()
    print(f"{fixed} of {len(packs)} 3D-map packs {'would change' if dry else 'updated'}")


if __name__ == "__main__":
    asyncio.run(main("--dry" in sys.argv))
