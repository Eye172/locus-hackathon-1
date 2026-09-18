"""The city a 3D campus map is locked to: the university's city boundary, plus a big city that directly borders it
when the campus sits right next to that border (Shenzhen + Hong Kong). Nothing else.

Boundaries come from OpenStreetMap through Nominatim (one request per second, cached for 30 days); neighbours from
Wikidata "shares border with" (P47) filtered by population.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union

from .. import cache, http
from ..geo import haversine_km
from ..models import University
from .sources import websearch

log = logging.getLogger("campuslens.citygeo")

SPARQL = "https://query.wikidata.org/sparql"
NEIGHBOUR_MIN_POP = 500_000
NEIGHBOUR_MAX_KM = 15.0          # the neighbour's boundary must be this close to the campus
NEIGHBOUR_CENTRE_KM = 45.0       # and its centre not much further
MIN_CITY_KM2 = 100.0            # smaller "cities" are districts or suburbs: look one level up
# ...unless OSM says the small area is a settlement in its own right: Karakol (39 km2) and Talas (14 km2) are towns,
# Hong Kong's "Central and Western" (20 km2, a suburb) and Almaty's Bostandyk (99 km2, a city district) are not.
# Without this a small town's scene was locked to its whole region, mountains and lakes included.
SETTLEMENTS = {"city", "town", "village", "municipality"}
CACHE_V = "v2:"                  # cached answers carry the OSM kind since v2
HUGE_KM2 = 6000.0                # municipalities like Chongqing: keep a 40 km disc around the campus
CITY_KINDS = {("boundary", "administrative"), ("place", "city"), ("place", "town"), ("place", "municipality"),
              ("boundary", "political"), ("place", "state")}


def _km2(g, lat: float) -> float:
    return g.area * 111.32 * 111.32 * max(math.cos(math.radians(lat)), 0.05)


def _deg_km(lat: float) -> float:
    return 111.32 * max(math.cos(math.radians(lat)), 0.2)


async def _paced_get(path: str, params: dict):
    """Nominatim allows one request per second: share the app-wide pacing lock."""
    async with websearch._nom_lock:  # noqa: SLF001 - one pacing lock for the whole process
        wait = 1.05 - (time.monotonic() - websearch._nom_last)  # noqa: SLF001
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await http.get(f"https://nominatim.openstreetmap.org/{path}", params={
                **params, "format": "jsonv2", "extratags": 1, "polygon_geojson": 1, "polygon_threshold": 0.002,
                "accept-language": "en"}, timeout=10.0)
            return r.json() if r.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            log.info("nominatim %s failed: %s", path, type(e).__name__)
            return None
        finally:
            websearch._nom_last = time.monotonic()  # noqa: SLF001


async def _nominatim(q: str) -> list[dict]:
    rows = await _paced_get("search", {"q": q, "limit": 8})
    return rows if isinstance(rows, list) else []


def _area_of(row: dict):
    g = row.get("geojson") or {}
    if g.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    try:
        s = shape(g)
        return s if s.is_valid else s.buffer(0)
    except Exception:  # noqa: BLE001
        return None


async def _city_shape(name: str, qid: str | None, near: tuple[float, float]):
    """(outline, name, wikidata id) of the city called `name` around `near`; Nominatim's wikidata tag decides."""
    key = CACHE_V + (qid or f"name:{name.lower()}:{round(near[0], 1)},{round(near[1], 1)}")
    hit = await cache.kv_get("citygeo", key, max_age_s=30 * 86400)
    if hit is not None:
        return (shape(hit["geom"]), hit["name"], hit.get("wd"), hit.get("kind")) if hit.get("geom") else None
    rows = await _nominatim(name)
    best, best_score = None, -1e9
    pt = Point(near[1], near[0])
    for row in rows:
        if (row.get("category"), row.get("type")) not in CITY_KINDS:
            continue
        g = _area_of(row)
        if g is None:
            continue
        wd = (row.get("extratags") or {}).get("wikidata")
        d_km = g.distance(pt) * _deg_km(near[0])
        if d_km > 25:
            continue
        rank = int(row.get("place_rank") or 30)
        score = (100 if qid and wd == qid else 0) - d_km * 3 - abs(rank - 14)
        if score > best_score:
            best, best_score = (g, row.get("name") or name, wd, row.get("addresstype")), score
    if rows:  # an empty answer may be a network hiccup: do not remember it
        await cache.kv_set("citygeo", key, {"geom": best[0].__geo_interface__, "name": best[1], "wd": best[2], "kind": best[3]}
                           if best else {"geom": None})
    return best


async def _containing_area(campus: tuple[float, float]):
    """The smallest administrative area around the campus that is city-sized (≥ MIN_CITY_KM2): Nominatim reverse
    at city, county and state zoom. Hong Kong universities are filed under 20 km² districts; this finds Hong Kong."""
    key = CACHE_V + f"rev:{round(campus[0], 3)},{round(campus[1], 3)}"
    hit = await cache.kv_get("citygeo", key, max_age_s=30 * 86400)
    if hit is not None:
        return (shape(hit["geom"]), hit["name"], hit.get("wd"), hit.get("kind")) if hit.get("geom") else None
    found, complete = None, True
    for zoom in (10, 8, 5):
        row = await _paced_get("reverse", {"lat": campus[0], "lon": campus[1], "zoom": zoom})
        if row is None:
            complete = False  # network failure: the answer is partial, do not remember it
            continue
        g = _area_of(row) if isinstance(row, dict) and "error" not in row else None
        if g is None:
            continue
        found = (g, row.get("name"), (row.get("extratags") or {}).get("wikidata"), row.get("addresstype"))
        if _km2(g, campus[0]) >= MIN_CITY_KM2:
            complete = True
            break
    if complete:
        await cache.kv_set("citygeo", key, {"geom": found[0].__geo_interface__, "name": found[1], "wd": found[2], "kind": found[3]}
                           if found else {"geom": None})
    return found


async def _neighbours(city_qid: str, campus: tuple[float, float]) -> list[dict]:
    q = f"""SELECT ?n ?pop ?coord ?label WHERE {{
  wd:{city_qid} wdt:P47 ?n .
  ?n wdt:P31/wdt:P279* wd:Q515 .
  ?n wdt:P1082 ?pop ; wdt:P625 ?coord .
  FILTER(?pop >= {NEIGHBOUR_MIN_POP})
  OPTIONAL {{ ?n rdfs:label ?label FILTER(lang(?label) = "en") }}
}} LIMIT 40"""
    try:
        d = await http.get_json(SPARQL, params={"query": q, "format": "json"},
                                headers={"Accept": "application/sparql-results+json"}, timeout=8.0)
    except Exception as e:  # noqa: BLE001
        log.info("P47 query failed: %s", type(e).__name__)
        return []
    out: dict[str, dict] = {}
    for b in d.get("results", {}).get("bindings", []):
        nid = b["n"]["value"].rsplit("/", 1)[-1]
        try:
            lon, lat = map(float, b["coord"]["value"].removeprefix("Point(").removesuffix(")").split())
        except ValueError:
            continue
        # a neighbour "right next to" the campus: its centre is close too (HK–Shenzhen yes, HK–Zhuhai over the sea no)
        if haversine_km(campus[0], campus[1], lat, lon) > NEIGHBOUR_CENTRE_KM:
            continue
        out.setdefault(nid, {"qid": nid, "name": (b.get("label") or {}).get("value"), "lat": lat, "lon": lon,
                             "pop": int(float(b["pop"]["value"]))})
    return sorted(out.values(), key=lambda n: haversine_km(campus[0], campus[1], n["lat"], n["lon"]))[:3]


async def city_area(uni: University, campus: tuple[float, float]) -> dict | None:
    """{names, bounds [s, w, n, e], rings [[[lat, lon]…]…]} or None when no boundary is known."""
    if not uni.city:
        return None
    near = (uni.city_lat, uni.city_lon) if uni.city_lat is not None else campus
    lat = campus[0]
    base = await _city_shape(", ".join(x for x in (uni.city, uni.country) if x), uni.city_qid, near)
    if base is not None and not base[0].buffer(0.02).contains(Point(campus[1], campus[0])):
        base = None  # the campus is not in the city it is filed under (a suburb campus)
    if base is None or (_km2(base[0], lat) < MIN_CITY_KM2 and base[3] not in SETTLEMENTS):
        around = await _containing_area(campus)
        if around is not None and (base is None or _km2(around[0], lat) > _km2(base[0], lat)):
            base = around
    if base is None:
        return None
    geom, name, city_wd, _kind = base
    names = [name]
    parts = [geom]
    for n in await _neighbours(city_wd or uni.city_qid, campus) if (city_wd or uni.city_qid) else []:
        if not n["name"]:
            continue
        other = await _city_shape(n["name"], n["qid"], (n["lat"], n["lon"]))
        if other is None:
            continue
        gap_km = other[0].distance(Point(campus[1], campus[0])) * _deg_km(lat)
        if gap_km <= NEIGHBOUR_MAX_KM:
            parts.append(other[0])
            names.append(other[1])
    area = unary_union(parts)
    if _km2(area, lat) > HUGE_KM2:
        disc = Point(campus[1], campus[0]).buffer(40 / _deg_km(lat))
        area = area.intersection(disc)
    # the campus itself must always be inside
    area = unary_union([area, Point(campus[1], campus[0]).buffer(3 / _deg_km(lat))]).simplify(0.0015)
    polys = [area] if isinstance(area, Polygon) else [g for g in getattr(area, "geoms", []) if isinstance(g, Polygon)]
    polys = sorted(polys, key=lambda g: -g.area)[:6]
    if not polys:
        return None
    mp = MultiPolygon(polys)
    minx, miny, maxx, maxy = mp.bounds
    return {
        "names": names,
        "bounds": [round(miny, 5), round(minx, 5), round(maxy, 5), round(maxx, 5)],
        "rings": [[[round(y, 5), round(x, 5)] for x, y in p.exterior.coords] for p in polys],
        "area_km2": round(_km2(mp, lat)),
        "source": "OpenStreetMap (Nominatim), Wikidata P47",
    }
