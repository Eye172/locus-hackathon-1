"""City context pack: route to the centre (OSRM), airport and railway, transit, POIs and campus building footprints.

All data comes from OpenStreetMap (Overpass) and the public OSRM demo router; everything is cached for 7 days.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .. import http
from ..geo import haversine_km
from ..models import Campus, University
from .sources.osm import overpass, kind_of

log = logging.getLogger("campuslens.city")

OSRM = "https://router.project-osrm.org/route/v1"
POI_KINDS = {
    "cafe": ("amenity", ["cafe", "coffee_shop"]), "restaurant": ("amenity", ["restaurant", "fast_food", "food_court"]),
    "supermarket": ("shop", ["supermarket", "convenience", "mall"]), "pharmacy": ("amenity", ["pharmacy"]),
    "bank": ("amenity", ["bank", "atm"]), "hospital": ("amenity", ["hospital", "clinic", "doctors"]),
    "cinema": ("amenity", ["cinema", "theatre"]), "park": ("leisure", ["park", "garden"]),
    "sports": ("leisure", ["fitness_centre", "sports_centre", "stadium", "swimming_pool"]),
    "library": ("amenity", ["library"]), "bus": ("highway", ["bus_stop"]), "rail": ("railway", ["station", "subway_entrance", "tram_stop"]),
}
POI_LABEL = {"cafe": "кафе", "restaurant": "рестораны и фастфуд", "supermarket": "магазины", "pharmacy": "аптеки", "bank": "банки и банкоматы",
             "hospital": "медицина", "cinema": "кино и театры", "park": "парки", "sports": "спорт", "library": "библиотеки",
             "bus": "остановки", "rail": "станции"}


def _q(lat: float, lon: float, campus: Campus | None) -> str:
    area = ""
    if campus and campus.osm_type and campus.osm_id:
        t = "way" if campus.osm_type == "way" else "relation"
        area = f'{t}({campus.osm_id}); map_to_area -> .a; way(area.a)["building"]; out geom 120;'
    return f"""[out:json][timeout:20];
{area}
(
  nwr(around:1000,{lat},{lon})["amenity"~"^(cafe|coffee_shop|restaurant|fast_food|food_court|pharmacy|bank|atm|hospital|clinic|doctors|cinema|theatre|library)$"];
  nwr(around:1000,{lat},{lon})["shop"~"^(supermarket|convenience|mall)$"];
  nwr(around:1000,{lat},{lon})["leisure"~"^(park|garden|fitness_centre|sports_centre|stadium|swimming_pool)$"];
  node(around:800,{lat},{lon})["highway"="bus_stop"];
  nwr(around:1500,{lat},{lon})["railway"~"^(station|subway_entrance|tram_stop)$"];
);
out tags center 300;
nwr(around:80000,{lat},{lon})["aeroway"="aerodrome"]["iata"]; out tags center 5;"""


async def _osrm(profile: str, a: tuple[float, float], b: tuple[float, float]) -> dict | None:
    url = f"{OSRM}/{profile}/{a[1]},{a[0]};{b[1]},{b[0]}"
    try:
        d = await http.get_json(url, params={"overview": "simplified", "geometries": "geojson"}, timeout=5.0)
        r = d["routes"][0]
        return {"distance_km": round(r["distance"] / 1000, 1), "minutes": round(r["duration"] / 60), "geometry": r["geometry"]}
    except Exception as e:  # noqa: BLE001
        log.info("osrm %s failed: %s", profile, e)
        return None


def _height(tags: dict) -> float:
    try:
        if tags.get("height"):
            return float(str(tags["height"]).replace("m", "").strip())
        if tags.get("building:levels"):
            return float(tags["building:levels"]) * 3.2
    except ValueError:
        pass
    return 10.0


async def build(uni: University, campus: Campus | None) -> dict:
    lat, lon = uni.lat, uni.lon
    data_task = asyncio.create_task(overpass(_q(lat, lon, campus), timeout=15.0))
    # The public OSRM demo only serves the car profile ("foot" silently returns car routes), so walking time
    # is estimated from the road distance at 4.6 km/h with a 15 % detour factor.
    routes: dict[str, dict | None] = {"drive": None, "walk": None}
    if uni.city_lat is not None and uni.city_lon is not None:
        routes["drive"] = await _osrm("driving", (lat, lon), (uni.city_lat, uni.city_lon))
        if routes["drive"]:
            routes["walk"] = {"minutes": round(routes["drive"]["distance_km"] / 4.6 * 60 * 1.15)}
    data = await data_task

    buildings, pois, aero = [], {}, []
    stops_800 = 0
    rail: list[dict] = []
    for el in (data or {}).get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "way" and "geometry" in el and tags.get("building"):
            ring = [[g["lon"], g["lat"]] for g in el["geometry"]]
            if len(ring) >= 4:
                buildings.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                                  "properties": {"id": el["id"], "kind": kind_of(tags), "name": tags.get("name:en") or tags.get("name"),
                                                 "height": _height(tags), "levels": tags.get("building:levels")}})
            continue
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None:
            continue
        if tags.get("aeroway") == "aerodrome":
            aero.append({"name": tags.get("name:en") or tags.get("name"), "iata": tags.get("iata"), "lat": c["lat"], "lon": c["lon"],
                         "distance_km": round(haversine_km(lat, lon, c["lat"], c["lon"]), 1)})
            continue
        if tags.get("highway") == "bus_stop":
            stops_800 += 1
            continue
        if tags.get("railway"):
            rail.append({"name": tags.get("name:en") or tags.get("name"), "kind": tags["railway"], "lat": c["lat"], "lon": c["lon"],
                         "distance_m": round(haversine_km(lat, lon, c["lat"], c["lon"]) * 1000)})
            continue
        for kind, (key, values) in POI_KINDS.items():
            if tags.get(key) in values:
                entry = pois.setdefault(kind, {"kind": kind, "label": POI_LABEL[kind], "count": 0, "items": []})
                entry["count"] += 1
                if len(entry["items"]) < 12:
                    entry["items"].append({"name": tags.get("name:en") or tags.get("name"), "lat": c["lat"], "lon": c["lon"],
                                           "distance_m": round(haversine_km(lat, lon, c["lat"], c["lon"]) * 1000)})
                break

    airport = None
    if aero:
        aero.sort(key=lambda a: a["distance_km"])
        airport = aero[0]
        r = await _osrm("driving", (lat, lon), (airport["lat"], airport["lon"]))
        if r:
            airport["drive_min"], airport["road_km"] = r["minutes"], r["distance_km"]
    rail.sort(key=lambda r: r["distance_m"])
    center_km = round(haversine_km(lat, lon, uni.city_lat, uni.city_lon), 1) if uni.city_lat is not None else None
    walk = routes["walk"]
    drive = routes["drive"]
    return {
        "center": {"name": uni.city, "lat": uni.city_lat, "lon": uni.city_lon, "population": uni.city_population},
        "route_center": {
            "distance_km": (drive or {}).get("distance_km") or center_km,
            "drive_min": (drive or {}).get("minutes"),
            "walk_min": (walk or {}).get("minutes") or (round(center_km / 4.6 * 60 * 1.15) if center_km else None),
            "walk_estimated": True,
            "geometry": (drive or walk or {}).get("geometry"),
            "source": "OSRM" if drive or walk else "прямая линия",
        },
        "airport": airport,
        "railway": rail[0] if rail else None,
        "transit": {"stops_800m": stops_800, "rail": rail[:5]},
        "poi": sorted(pois.values(), key=lambda p: -p["count"]),
        "campus_buildings": {"type": "FeatureCollection", "features": buildings},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {"label": "OpenStreetMap", "url": campus.osm_url if campus and campus.osm_url else "https://www.openstreetmap.org/"},
            {"label": "OSRM", "url": "https://project-osrm.org/"},
        ],
    }
