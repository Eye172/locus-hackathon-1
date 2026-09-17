"""Level-of-detail data for the globe (planet → country → city):

    frontend/public/geo/countries.json      one row per country: names, count, centre, bbox          (level 1)
    frontend/public/geo/c/{ISO}.json        that country's cities (groups of universities) + points  (levels 2-3)

A "city" is built from the Wikidata city label when it names a real settlement; district / street labels and
missing labels are attached to the nearest named city (≤ 20 km); what is left is grouped by distance (8 km) and
named by reverse geocoding (cached). With --geocode, universities without coordinates in the chosen countries
are looked up in OpenStreetMap by name (only university/college objects inside the country are accepted).

    python scripts/build_geo_levels.py                      # build files (uses caches, no geocoding)
    python scripts/build_geo_levels.py --geocode KZ KG UZ TJ TM
    python scripts/build_geo_levels.py --reverse            # also reverse-geocode unnamed groups (1 req/s)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_geojson import COUNTRY_META  # noqa: E402  (QID -> ISO, ru, en, kk)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "data"
SRC = DATA / "universities.json"
GEOCODE_CACHE = DATA / "geocode_cache.json"
REVERSE_CACHE = DATA / "reverse_city_cache.json"
OUT = ROOT / "frontend" / "public" / "geo"
UA = {"User-Agent": "CampusLens/0.1 (LOCUS hackathon; contact: nnurkhan91@gmail.com)"}

NOT_A_CITY = re.compile(
    r"район|district|округ|микрорайон|мкр|проспект|улица|avenue|street|borough|county|ward|arrondissement|bezirk|"
    r"stadtteil|ortsteil|quarter|neighbou?rhood|municipality of|муниципальн|поселение|сельсовет|волость|"
    r"область|oblast|province|region|регион|штат|state of|prefecture|префектура|провинция|губерния|край\b|"
    r"университет|university|campus|кампус|\d", re.I)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(h)))


def components(points: list[tuple[float, float]], link_km: float) -> list[list[int]]:
    """Single-linkage groups (grid-accelerated)."""
    cell = link_km / 111.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (la, lo) in enumerate(points):
        grid[(int(la // cell), int(lo // cell))].append(i)
    parent = list(range(len(points)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, (la, lo) in enumerate(points):
        gx, gy = int(la // cell), int(lo // cell)
        lat_scale = max(0.2, math.cos(math.radians(la)))
        span = int(math.ceil(1 / lat_scale))
        for dx in (-1, 0, 1):
            for dy in range(-span, span + 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j > i and km(points[i], points[j]) <= link_km:
                        parent[find(i)] = find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(points)):
        groups[find(i)].append(i)
    return list(groups.values())


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def nominatim(client: httpx.Client, path: str, params: dict) -> list | dict | None:
    for attempt in range(3):
        time.sleep(1.1)  # usage policy: 1 request per second
        try:
            r = client.get(f"https://nominatim.openstreetmap.org/{path}", params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5)
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return None


# one name per city (Wikidata still labels some cities by their old or English names)
CITY_SYNONYMS = {"Алма-Ата": "Алматы", "Almaty": "Алматы", "Нур-Султан": "Астана", "Nur-Sultan": "Астана", "Astana": "Астана",
                 "Целиноград": "Астана", "Семипалатинск": "Семей", "Semey": "Семей", "Shymkent": "Шымкент", "Чимкент": "Шымкент",
                 "Oral": "Уральск", "Орал": "Уральск", "Öskemen": "Усть-Каменогорск", "Oskemen": "Усть-Каменогорск",
                 "Bishkek": "Бишкек", "Фрунзе": "Бишкек", "Tashkent": "Ташкент", "Toshkent": "Ташкент", "Dushanbe": "Душанбе"}

UNI_TYPES = {("amenity", "university"), ("amenity", "college"), ("building", "university"), ("building", "college"),
             ("office", "educational_institution"), ("amenity", "school")}


def geocode_missing(rows: list[dict], isos: set[str], client: httpx.Client) -> int:
    iso_of = {q: m[0] for q, m in COUNTRY_META.items()}
    cache = load_json(GEOCODE_CACHE, {})
    added = 0
    todo = [r for r in rows if not r.get("coord") and iso_of.get(r["country"]) in isos]
    print(f"geocoding {len(todo)} universities without coordinates", flush=True)
    for r in todo:
        key = r["id"]
        if key not in cache:
            hit = None
            for name in [r.get("ru"), r.get("en")] + list(r.get("aliases") or [])[:2]:
                if not name:
                    continue
                res = nominatim(client, "search", {"q": name, "format": "jsonv2", "limit": 5,
                                                   "countrycodes": iso_of[r["country"]].lower(), "addressdetails": 1})
                for x in res or []:
                    if (x.get("category"), x.get("type")) in UNI_TYPES:
                        addr = x.get("address") or {}
                        hit = {"lat": float(x["lat"]), "lon": float(x["lon"]), "name": x.get("name"),
                               "city": addr.get("city") or addr.get("town") or addr.get("village")}
                        break
                if hit:
                    break
            cache[key] = hit
            GEOCODE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        hit = cache.get(key)
        if hit:
            r["coord"] = [round(hit["lat"], 6), round(hit["lon"], 6)]
            r["coord_src"] = "osm"
            if hit.get("city") and (not r.get("city") or NOT_A_CITY.search(r["city"])):
                r["city"] = hit["city"]
            added += 1
    print(f"  found {added} of {len(todo)}", flush=True)
    return added


def build_country(iso: str, rows: list[dict], reverse: bool, client: httpx.Client | None, rcache: dict,
                  fix: bool = False, fixed: list | None = None) -> dict:
    fixed = fixed if fixed is not None else []
    pts = [(r["coord"][0], r["coord"][1]) for r in rows]
    good = [CITY_SYNONYMS.get(r["city"], r["city"]) if r.get("city") and not NOT_A_CITY.search(r["city"]) else None for r in rows]
    cities: list[dict] = []
    member_of = [-1] * len(rows)
    # 1. named cities: rows sharing a good label, split where the same name is far apart (two Springfields)
    by_label: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(good):
        if g:
            by_label[g].append(i)
    for label, idx in by_label.items():
        comps = sorted(([idx[k] for k in comp] for comp in components([pts[i] for i in idx], 30.0)), key=len, reverse=True)
        groups: list[dict] = []
        for members in comps:
            cc = (statistics.median(pts[m][0] for m in members), statistics.median(pts[m][1] for m in members))
            home = next((g for g in groups if km(cc, g["core_c"]) <= 100), None)
            if home is not None:           # same city name nearby: a misplaced coordinate or a suburb campus
                home["members"] += members
                home["outliers"] += members
                continue
            groups.append({"name": label, "members": list(members), "core": list(members), "core_c": cc, "outliers": []})
        cities.extend(groups)

    def centre(c: dict) -> tuple[float, float]:
        core = c.get("core") or c["members"]
        return (statistics.median(pts[m][0] for m in core), statistics.median(pts[m][1] for m in core))

    # 1b. optional: re-geocode outliers by name (only accepted when OSM puts the university inside its city)
    if fix and client is not None:
        for c in cities:
            for m in list(c.get("outliers", [])):
                r = rows[m]
                cc = centre(c)
                for name in [r.get("ru"), r.get("en")]:
                    if not name:
                        continue
                    res = nominatim(client, "search", {"q": name, "format": "jsonv2", "limit": 5, "countrycodes": iso.lower()})
                    hit = next((x for x in res or [] if (x.get("category"), x.get("type")) in UNI_TYPES
                                and km((float(x["lat"]), float(x["lon"])), cc) <= 30), None)
                    if hit:
                        pts[m] = (float(hit["lat"]), float(hit["lon"]))
                        r["coord"] = [round(pts[m][0], 6), round(pts[m][1], 6)]
                        r["coord_src"] = "osm-fix"
                        c["outliers"].remove(m)
                        c["core"].append(m)
                        fixed.append(r["id"])
                        print(f"  fixed {r['id']} {name[:40]} -> inside {c['name']}", flush=True)
                        break

    # 2. merge named cities whose centres nearly coincide (Алма-Ата / Алматы): the bigger one keeps its name
    changed = True
    while changed:
        changed = False
        centres = [centre(c) for c in cities]
        for a in range(len(cities)):
            for b in range(a + 1, len(cities)):
                if cities[a]["members"] and cities[b]["members"] and km(centres[a], centres[b]) < 4:
                    big, small = (a, b) if len(cities[a]["members"]) >= len(cities[b]["members"]) else (b, a)
                    cities[big]["members"] += cities[small]["members"]
                    cities[big].setdefault("core", []).extend(cities[small].get("core") or cities[small]["members"])
                    cities[big].setdefault("outliers", []).extend(cities[small].get("outliers", []))
                    cities[small]["members"] = []
                    changed = True
        cities = [c for c in cities if c["members"]]
    for ci, c in enumerate(cities):
        for m in c["members"]:
            member_of[m] = ci
    # 3. unnamed / district rows join the nearest named city within 20 km
    centres = [centre(c) for c in cities]
    rest = []
    for i in range(len(rows)):
        if member_of[i] >= 0:
            continue
        best = min(range(len(cities)), key=lambda k: km(pts[i], centres[k]), default=None)
        if best is not None and km(pts[i], centres[best]) <= 20:
            cities[best]["members"].append(i)
            cities[best].setdefault("core", []).append(i)
            member_of[i] = best
        else:
            rest.append(i)
    # 4. leftovers grouped by distance; named by the commonest (district) label or by reverse geocoding
    for comp in components([pts[i] for i in rest], 8.0):
        members = [rest[k] for k in comp]
        labels = Counter(rows[m].get("city") for m in members if rows[m].get("city"))
        c = {"name": None, "members": members}
        cc = (statistics.median(pts[m][0] for m in members), statistics.median(pts[m][1] for m in members))
        key = f"{cc[0]:.2f},{cc[1]:.2f}"
        if key in rcache:
            c["name"] = rcache[key]
        elif reverse and client is not None:
            res = nominatim(client, "reverse", {"lat": cc[0], "lon": cc[1], "format": "jsonv2", "zoom": 10,
                                                "accept-language": "ru"})
            addr = (res or {}).get("address") or {}
            name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or addr.get("county")
            rcache[key] = name
            c["name"] = name
            REVERSE_CACHE.write_text(json.dumps(rcache, ensure_ascii=False, indent=0), encoding="utf-8")
        if not c["name"] and labels:
            c["name"] = labels.most_common(1)[0][0]
        cities.append(c)
    out_cities, out_unis = [], []
    cities.sort(key=lambda c: -len(c["members"]))
    for cid, c in enumerate(cities):
        core = c.get("core") or c["members"]
        la = [pts[m][0] for m in core]
        lo = [pts[m][1] for m in core]
        clat, clon = statistics.median(la), statistics.median(lo)
        pad_lat = 2.0 / 111
        pad_lon = 2.0 / (111 * max(0.2, math.cos(math.radians(clat))))
        s, n = min(la) - pad_lat, max(la) + pad_lat
        w, e = min(lo) - pad_lon, max(lo) + pad_lon
        min_lat, min_lon = 4.0 / 111, 4.0 / (111 * max(0.2, math.cos(math.radians(clat))))  # at least ~8 km across
        if n - s < 2 * min_lat:
            s, n = clat - min_lat, clat + min_lat
        if e - w < 2 * min_lon:
            w, e = clon - min_lon, clon + min_lon
        out_cities.append({"id": cid, "name": c["name"] or "", "lat": round(clat, 5), "lon": round(clon, 5),
                           "bbox": [round(s, 4), round(w, 4), round(n, 4), round(e, 4)], "count": len(c["members"])})
        for m in c["members"]:
            r = rows[m]
            u = {"qid": r["id"], "name": r.get("ru") or r.get("en") or r["id"], "lat": round(pts[m][0], 5),
                 "lon": round(pts[m][1], 5), "c": cid}
            if r.get("en") and r.get("en") != u["name"]:
                u["name_en"] = r["en"]
            if r.get("kk"):
                u["name_kk"] = r["kk"]
            out_unis.append(u)
    return {"iso": iso, "cities": out_cities, "unis": out_unis}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geocode", nargs="*", default=[], help="ISO codes whose missing coordinates are looked up")
    ap.add_argument("--reverse", action="store_true", help="reverse-geocode names of unnamed groups")
    ap.add_argument("--fix-outliers", nargs="*", default=[], help="ISO codes whose misplaced universities are re-geocoded")
    args = ap.parse_args()
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    client = httpx.Client(headers=UA, timeout=20) if (args.geocode or args.reverse or args.fix_outliers) else None
    fix_set = {x.upper() for x in args.fix_outliers}
    fixed: list[str] = []
    if args.geocode:
        if geocode_missing(rows, {x.upper() for x in args.geocode}, client):
            SRC.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print("universities.json updated", flush=True)
    rcache = load_json(REVERSE_CACHE, {})
    (OUT / "c").mkdir(parents=True, exist_ok=True)
    countries = []
    unnamed_total = 0
    for qid, (iso, ru, en, kk) in COUNTRY_META.items():
        crow = [r for r in rows if r["country"] == qid and r.get("coord")
                and -90 <= r["coord"][0] <= 90 and -180 <= r["coord"][1] <= 180]
        if not crow:
            continue
        data = build_country(iso, crow, args.reverse, client, rcache, iso in fix_set, fixed)
        unnamed_total += sum(1 for c in data["cities"] if not c["name"])
        (OUT / "c" / f"{iso}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        lats = sorted(u["lat"] for u in data["unis"])
        lons = sorted(u["lon"] for u in data["unis"])
        k = max(0, int(len(lats) * 0.04))
        countries.append({"qid": qid, "iso": iso, "ru": ru, "en": en, "kk": kk, "count": len(data["unis"]),
                          "cities": len(data["cities"]),
                          "center": [round(statistics.median(lats), 3), round(statistics.median(lons), 3)],
                          "bbox": [round(lats[k], 3), round(lons[k], 3), round(lats[-1 - k], 3), round(lons[-1 - k], 3)]})
    if fixed:
        SRC.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"universities.json updated: {len(fixed)} coordinates fixed", flush=True)
    countries.sort(key=lambda c: -c["count"])
    (OUT / "countries.json").write_text(json.dumps(countries, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sizes = sorted(((p.stat().st_size, p.name) for p in (OUT / "c").glob("*.json")), reverse=True)
    print(f"countries: {len(countries)} | total universities on the map: {sum(c['count'] for c in countries)} | "
          f"unnamed city groups: {unnamed_total} | biggest files: {[(n, s // 1024) for s, n in sizes[:3]]} KB", flush=True)


if __name__ == "__main__":
    main()
