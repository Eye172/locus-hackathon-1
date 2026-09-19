"""Give every index row whose "en" label is not really English (Hochschule Mittweida, Universidad del Este) or is
missing an English display name in `name_en`: an English alias when the row has one, otherwise a Gemini translation
(cached in kv 'name_en', so profile builds of the same university reuse it). Then run export_geojson.py.

    python scripts/english_names.py [--dry]
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.pipeline import names  # noqa: E402
from export_geojson import COUNTRY_META  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parents[1] / "data" / "universities.json"
BATCH = 50


async def main(dry: bool) -> None:
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    todo: list[dict] = []
    from_alias = 0
    for r in rows:
        r.pop("name_en", None)
        if names.is_english(r.get("en")):
            continue
        alt = names.english_alternative(r.get("en"), r.get("aliases") or [])
        if alt:
            r["name_en"] = alt
            from_alias += 1
        else:
            todo.append(r)
    print(f"{from_alias} from aliases, {len(todo)} to translate")
    src = {r["id"]: r.get("en") or r.get("ru") or r.get("kk") for r in todo}
    missing = []
    for r in todo:
        hit = await cache.kv_get("name_en", src[r["id"]])
        if hit and names.is_english(hit.get("en")):  # a cached answer still in the own language is asked again
            r["name_en"] = hit["en"]
        else:
            missing.append(r)
    print(f"{len(todo) - len(missing)} cached, {len(missing)} for Gemini")
    if dry:
        return
    sem = asyncio.Semaphore(4)

    async def run(batch: list[dict]) -> None:
        async with sem:
            items = [(src[r["id"]], COUNTRY_META.get(r["country"], (None, None, None))[2]) for r in batch]
            try:
                out = await names.translate(items, timeout=90.0)
            except Exception as e:  # noqa: BLE001
                print(f"batch failed: {e!r}")
                return
            for r, en in zip(batch, out):
                if en:
                    r["name_en"] = en
                    await cache.kv_set("name_en", src[r["id"]], {"en": en})
            print(f"batch of {len(batch)}: {sum(1 for x in out if x)} named", flush=True)

    await asyncio.gather(*[run(missing[i:i + BATCH]) for i in range(0, len(missing), BATCH)])
    SRC.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"wrote {sum(1 for r in rows if r.get('name_en'))} English names to {SRC}")


if __name__ == "__main__":
    asyncio.run(main("--dry" in sys.argv))
