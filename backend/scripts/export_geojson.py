"""Export the offline university index as a compact GeoJSON for the globe (frontend/public/universities.geojson)
and a countries.json with centres/bboxes computed from the points."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.pipeline.names import row_en, row_name  # noqa: E402
SRC = ROOT / "backend" / "data" / "universities.json"
OUT = ROOT / "frontend" / "public" / "universities.geojson"
OUT_C = ROOT / "frontend" / "public" / "countries.json"

COUNTRY_META = {  # QID -> ISO2, names
    "Q232": ("KZ", "Казахстан", "Kazakhstan", "Қазақстан"), "Q813": ("KG", "Кыргызстан", "Kyrgyzstan", "Қырғызстан"),
    "Q265": ("UZ", "Узбекистан", "Uzbekistan", "Өзбекстан"), "Q863": ("TJ", "Таджикистан", "Tajikistan", "Тәжікстан"),
    "Q874": ("TM", "Туркменистан", "Turkmenistan", "Түрікменстан"), "Q159": ("RU", "Россия", "Russia", "Ресей"),
    "Q43": ("TR", "Турция", "Turkey", "Түркия"), "Q30": ("US", "США", "United States", "АҚШ"),
    "Q145": ("GB", "Великобритания", "United Kingdom", "Ұлыбритания"), "Q183": ("DE", "Германия", "Germany", "Германия"),
    "Q16": ("CA", "Канада", "Canada", "Канада"), "Q148": ("CN", "Китай", "China", "Қытай"),
    "Q884": ("KR", "Южная Корея", "South Korea", "Оңтүстік Корея"), "Q17": ("JP", "Япония", "Japan", "Жапония"),
    "Q878": ("AE", "ОАЭ", "UAE", "БАӘ"), "Q213": ("CZ", "Чехия", "Czechia", "Чехия"), "Q36": ("PL", "Польша", "Poland", "Польша"),
    "Q38": ("IT", "Италия", "Italy", "Италия"), "Q142": ("FR", "Франция", "France", "Франция"),
    "Q55": ("NL", "Нидерланды", "Netherlands", "Нидерланд"), "Q408": ("AU", "Австралия", "Australia", "Аустралия"),
    "Q833": ("MY", "Малайзия", "Malaysia", "Малайзия"), "Q334": ("SG", "Сингапур", "Singapore", "Сингапур"),
    "Q184": ("BY", "Беларусь", "Belarus", "Беларусь"), "Q227": ("AZ", "Азербайджан", "Azerbaijan", "Әзірбайжан"),
    "Q230": ("GE", "Грузия", "Georgia", "Грузия"), "Q39": ("CH", "Швейцария", "Switzerland", "Швейцария"),
    "Q34": ("SE", "Швеция", "Sweden", "Швеция"), "Q31": ("BE", "Бельгия", "Belgium", "Бельгия"),
    "Q40": ("AT", "Австрия", "Austria", "Австрия"), "Q45": ("PT", "Португалия", "Portugal", "Португалия"),
    "Q29": ("ES", "Испания", "Spain", "Испания"), "Q79": ("EG", "Египет", "Egypt", "Мысыр"),
    "Q668": ("IN", "Индия", "India", "Үндістан"), "Q219": ("BG", "Болгария", "Bulgaria", "Болгария"),
    "Q28": ("HU", "Венгрия", "Hungary", "Мажарстан"), "Q211": ("LV", "Латвия", "Latvia", "Латвия"),
    "Q37": ("LT", "Литва", "Lithuania", "Литва"), "Q191": ("EE", "Эстония", "Estonia", "Эстония"),
    "Q20": ("NO", "Норвегия", "Norway", "Норвегия"), "Q35": ("DK", "Дания", "Denmark", "Дания"),
    "Q33": ("FI", "Финляндия", "Finland", "Финляндия"), "Q27": ("IE", "Ирландия", "Ireland", "Ирландия"),
    "Q212": ("UA", "Украина", "Ukraine", "Украина"),
}


def main() -> None:
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    feats = []
    by_country: dict[str, list[tuple[float, float]]] = {}
    counts: dict[str, int] = {}
    for r in rows:
        iso = COUNTRY_META.get(r["country"], (None,))[0]
        counts[iso] = counts.get(iso, 0) + 1
        c = r.get("coord")
        if not c or not iso:
            continue
        lat, lon = c
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        by_country.setdefault(iso, []).append((lat, lon))
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {k: v for k, v in {
                # Russian if the university has a Russian name, else English (see app/pipeline/names.py)
                "qid": r["id"], "name": row_name(r), "name_en": row_en(r) or r.get("ru"),
                "name_kk": r.get("kk"), "city": r.get("city"), "c": iso,
            }.items() if v},
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    countries = []
    for qid, (iso, ru, en, kk) in COUNTRY_META.items():
        pts = by_country.get(iso, [])
        if not pts:
            continue
        lats = sorted(p[0] for p in pts)
        lons = sorted(p[1] for p in pts)
        # trimmed bbox (drop 5% outliers such as overseas territories)
        k = max(0, int(len(pts) * 0.05))
        bbox = [lats[k], lons[k], lats[-1 - k], lons[-1 - k]]
        countries.append({"qid": qid, "iso": iso, "ru": ru, "en": en, "kk": kk, "count": counts.get(iso, 0),
                          "with_coords": len(pts), "center": [round(sum(lats) / len(lats), 3), round(sum(lons) / len(lons), 3)],
                          "bbox": [round(x, 3) for x in bbox]})
    countries.sort(key=lambda c: -c["count"])
    OUT_C.write_text(json.dumps(countries, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"geojson: {len(feats)} points -> {OUT} ({OUT.stat().st_size // 1024} KB); countries: {len(countries)}")


if __name__ == "__main__":
    main()
