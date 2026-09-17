"""OpenStreetMap via Overpass: campus polygon, tagged buildings inside, transport stops."""
from __future__ import annotations

import asyncio
import logging
import re

from rapidfuzz import fuzz

from ... import http
from ...config import settings
from ...geo import bbox_around, bbox_from_points, polygon_from_ways
from ...models import Building, Campus

log = logging.getLogger("campuslens.osm")

AMENITY_RE = "^(library|university|college|cafe|canteen|fast_food|food_court|dormitory)$"
LEISURE_RE = "^(sports_centre|pitch|stadium|swimming_pool|fitness_centre|track|sports_hall)$"


def _q_by_wikidata(qid: str) -> str:
    return f"""[out:json][timeout:15];
nwr["wikidata"="{qid}"]->.u;
.u out geom;
.u map_to_area -> .a;
(
  way(area.a)["building"];
  nwr(area.a)["amenity"~"{AMENITY_RE}"];
  nwr(area.a)["leisure"~"{LEISURE_RE}"];
);
out tags center;"""


def _q_nearby(lat: float, lon: float, radius: int = 700) -> str:
    return f"""[out:json][timeout:15];
nwr["amenity"~"^(university|college)$"](around:{radius},{lat},{lon});
out tags geom;"""


def _q_by_id(osm_type: str, osm_id: int) -> str:
    return f"""[out:json][timeout:15];
{osm_type}({osm_id})->.u;
.u out geom;
.u map_to_area -> .a;
(
  way(area.a)["building"];
  nwr(area.a)["amenity"~"{AMENITY_RE}"];
  nwr(area.a)["leisure"~"{LEISURE_RE}"];
);
out tags center;"""


async def overpass(query: str, timeout: float | None = None) -> dict | None:
    timeout = timeout or settings.overpass_timeout_s
    last = None
    for url in settings.overpass_urls:
        for attempt in range(2):
            try:
                r = await http.post(url, data={"data": query}, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                last = f"{url} -> {r.status_code}"
                if r.status_code in (429, 504) and attempt == 0:
                    await asyncio.sleep(1.5)  # rate-limited: one short retry
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last = f"{url} -> {type(e).__name__}"
                break
    log.warning("overpass failed: %s", last)
    return None


def kind_of(tags: dict) -> str:
    b = tags.get("building", "")
    a = tags.get("amenity", "")
    l = tags.get("leisure", "")
    if b == "dormitory" or a == "dormitory":
        return "dormitory"
    if a == "library" or b == "library":
        return "library"
    if l or b in ("sports_hall", "stadium", "grandstand", "sports_centre") or tags.get("sport"):
        return "sports"
    if a in ("cafe", "canteen", "fast_food", "food_court"):
        return "student_life"
    if b in ("university", "college", "school") or a in ("university", "college"):
        return "academic"
    return "other"


def _polygon_of(el: dict) -> list[list[float]] | None:
    if el["type"] == "way" and el.get("geometry"):
        pts = [[g["lat"], g["lon"]] for g in el["geometry"]]
        return pts if len(pts) >= 4 else None
    if el["type"] == "relation" and el.get("members"):
        outers = [[(g["lat"], g["lon"]) for g in m.get("geometry", [])]
                  for m in el["members"] if m.get("role") in ("outer", "") and m.get("geometry")]
        return polygon_from_ways(outers)
    return None


def _parse(data: dict, campus_el: dict | None, lat: float, lon: float) -> Campus:
    polygon = _polygon_of(campus_el) if campus_el else None
    buildings: list[Building] = []
    counts: dict[str, int] = {}
    campus_id = campus_el["id"] if campus_el else None
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        if not tags or el.get("id") == campus_id or "geometry" in el or "members" in el:
            continue
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None:
            continue
        kind = kind_of(tags)
        counts[kind] = counts.get(kind, 0) + 1
        if len(buildings) < 200:
            buildings.append(Building(
                osm_id=f"{el['type']}/{el['id']}", name=tags.get("name"),
                name_en=tags.get("name:en") or tags.get("name:ru"), kind=kind,
                lat=c["lat"], lon=c["lon"],
            ))
    if polygon:
        bbox = bbox_from_points([(p[0], p[1]) for p in polygon])
        mode = "polygon"
    else:
        bbox = bbox_around(lat, lon, 500)
        mode = "radius"
    return Campus(
        osm_type=campus_el["type"] if campus_el else None,
        osm_id=campus_el["id"] if campus_el else None,
        osm_url=f"https://www.openstreetmap.org/{campus_el['type']}/{campus_el['id']}" if campus_el else None,
        polygon=polygon, bbox=bbox, mode=mode, radius_m=500.0,
        buildings=buildings, counts=counts,
    )


async def nominatim_outline(qid: str, names: list[str], lat: float, lon: float) -> Campus | None:
    """Fallback when Overpass is busy: Nominatim returns the same OSM outline (no buildings) in ~0.4 s."""
    for name in names[:2]:
        try:
            d = await http.get_json("https://nominatim.openstreetmap.org/search", params={
                "q": name, "format": "jsonv2", "limit": 5, "extratags": 1, "polygon_geojson": 1,
            }, timeout=4.0)
        except Exception:
            continue
        for x in d or []:
            tags = x.get("extratags") or {}
            near = abs(float(x["lat"]) - lat) < 0.05 and abs(float(x["lon"]) - lon) < 0.08
            if tags.get("wikidata") != qid and not (x.get("type") in ("university", "college") and near):
                continue
            g = x.get("geojson") or {}
            ring = None
            if g.get("type") == "Polygon":
                ring = g["coordinates"][0]
            elif g.get("type") == "MultiPolygon":
                ring = max((p[0] for p in g["coordinates"]), key=len)
            if not ring or len(ring) < 4:
                continue
            polygon = [[c[1], c[0]] for c in ring]
            return Campus(
                osm_type=x.get("osm_type"), osm_id=int(x["osm_id"]),
                osm_url=f"https://www.openstreetmap.org/{x.get('osm_type')}/{x.get('osm_id')}",
                polygon=polygon, bbox=bbox_from_points([(p[0], p[1]) for p in polygon]), mode="polygon",
            )
    return None


async def campus(qid: str, lat: float, lon: float, aliases: list[str]) -> Campus:
    """Find the campus outline and everything tagged inside it. Falls back to Nominatim, then to a 500 m radius."""
    # web-found universities (W…) have no wikidata tag: go straight to the outline by name / nearby search
    data = await overpass(_q_by_wikidata(qid)) if qid.startswith("Q") else None
    campus_el = None
    if data:
        outlines = [el for el in data.get("elements", []) if _polygon_of(el)]
        if outlines:
            campus_el = max(outlines, key=lambda el: len(el.get("geometry") or el.get("members") or []))
    if campus_el is None and data is None:
        via_nominatim = await nominatim_outline(qid, aliases, lat, lon)
        if via_nominatim:
            log.info("campus outline via Nominatim for %s", qid)
            return via_nominatim
    if campus_el is None:
        near = await overpass(_q_nearby(lat, lon))
        best, best_score = None, 0
        for el in (near or {}).get("elements", []):
            if not _polygon_of(el):
                continue
            names = [v for k, v in (el.get("tags") or {}).items() if k.startswith("name")]
            score = max((fuzz.token_set_ratio(n, a) for n in names for a in aliases), default=0)
            if score > best_score:
                best, best_score = el, score
        if best is not None and best_score >= 60:
            data = await overpass(_q_by_id(best["type"], best["id"]))
            campus_el = best
        else:
            data = {"elements": []}
    return _parse(data or {"elements": []}, campus_el, lat, lon)


async def transport_stops(lat: float, lon: float, radius_m: int = 800) -> int | None:
    q = f"""[out:json][timeout:10];
(
  node(around:{radius_m},{lat},{lon})["highway"="bus_stop"];
  node(around:{radius_m},{lat},{lon})["public_transport"~"^(platform|stop_position)$"];
  node(around:{radius_m},{lat},{lon})["railway"~"^(station|subway_entrance|tram_stop)$"];
);
out ids;"""
    data = await overpass(q, timeout=6.0)
    if data is None:
        return None
    return len(data.get("elements", []))
