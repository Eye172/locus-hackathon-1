"""3D campus map pack: campus outline, building footprints with heights, dormitories and nearby places.

Overpass is often unreachable from here, so the same OpenStreetMap data is read from the OpenFreeMap planet
vector tiles (zoom 14): the `building` layer gives footprints with render heights, the `poi` layer gives named
places (dormitories, cafés, shops, museums…). Tiles are cached on disk. Elevations come from Open-Meteo
(Copernicus DEM) because the 3D camera needs the ground height of the point it looks at.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mapbox_vector_tile
from mapbox_vector_tile.Mapbox import vector_tile_pb2
from rapidfuzz import fuzz
from shapely import STRtree
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.validation import make_valid

from .. import cache, http
from ..config import settings
from ..geo import bbox_around, haversine_km
from ..models import Campus, University

log = logging.getLogger("campuslens.map3d")

TILEJSON = "https://tiles.openfreemap.org/planet"
Z = 14
TILE_TTL_S = 14 * 86400
DORM_RADIUS_M = 2500
POI_RADIUS_M = 2000
MAX_CAMPUS_BUILDINGS = 400
PACK_VERSION = 4  # 2: city_area (camera locked to the city); 3: small towns are cities, not their region;
#   4: packs built while the elevation service refused (camera target at 0 m) are not reused
# The city boundary (Nominatim, paced at 1 request/s and shared with the profile build) took 2-10 s on its own, up to
# 25 s next to a live profile build, and the whole pack waited for it: the 3D scene stood without its campus. The
# camera is locked to the city only after the ~16 s orbit, so the pack goes out without it (city_status "pending")
# and the client asks again; the lookup keeps running meanwhile.
CITY_WAIT_S = 0.6  # a boundary cached from an earlier visit is back in ~50 ms

DORM_NAME_RE = re.compile(r"общежит|жатақхана|жатакхана|dormitor|residence hall|student (house|housing|residence)|"
                          r"студенческ\w* (дом|городок)|кампус.*корпус проживания|halls? of residence", re.I)

# OpenMapTiles poi class/subclass -> map layer
GROUPS: dict[str, dict[str, set[str]]] = {
    "cafe": {"class": {"cafe", "ice_cream"}, "subclass": {"cafe", "coffee", "ice_cream", "bakery", "pastry", "confectionery", "tea"}},
    "food": {"class": {"restaurant", "fast_food", "food_court"}, "subclass": {"restaurant", "fast_food", "food_court", "canteen"}},
    "shops": {"class": {"grocery", "shop", "clothing_store", "department_store"},
              "subclass": {"supermarket", "convenience", "mall", "department_store", "marketplace", "general", "variety_store",
                           "books", "clothes", "shoes", "greengrocer", "deli", "butcher", "stationery"}},
    "fun": {"class": {"cinema", "bar", "beer", "entertainment"},
            "subclass": {"cinema", "nightclub", "bowling_alley", "amusement_arcade", "escape_game", "karaoke_box", "theme_park",
                         "water_park", "zoo", "bar", "pub", "biergarten", "casino", "trampoline_park"}},
    "culture": {"class": {"museum", "theatre", "art_gallery", "library", "monument", "castle"},
                "subclass": {"museum", "theatre", "arts_centre", "gallery", "library", "planetarium", "concert_hall",
                             "community_centre", "monument", "memorial", "place_of_worship"}},
    "park": {"class": {"park"}, "subclass": {"park", "garden", "nature_reserve"}},
    "sport": {"class": {"stadium", "sports", "swimming"},
              "subclass": {"fitness_centre", "sports_centre", "stadium", "swimming_pool", "sports_hall", "ice_rink", "fitness"}},
    "transit": {"class": {"railway", "bus"}, "subclass": {"station", "subway_entrance", "tram_stop", "bus_stop", "bus_station", "halt"}},
    "health": {"class": {"pharmacy", "hospital", "doctors", "dentist"},
               "subclass": {"pharmacy", "hospital", "clinic", "doctors", "dentist", "chemist"}},
}

_tile_tpl: tuple[str, float] | None = None
_elev_memo: dict[tuple[float, float], float] = {}
# single-flight lookups: a request that needs one already running joins it instead of starting another.
# Tiles leave the table when done (the disk cache holds them); outlines and city boundaries stay (small).
_tasks: dict[object, asyncio.Task] = {}
# the map's own worker: in the shared default pool it queued behind a live profile build's hundreds of image decodes
# (a 1.5 s analysis took 26 s)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="map3d")


async def _in_pool(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)


def _shared(key: object, make, keep: bool = True) -> asyncio.Task:
    t = _tasks.get(key)
    if t is None or (t.done() and (t.cancelled() or t.exception() is not None)):
        t = _tasks[key] = asyncio.ensure_future(make())
        if not keep:
            t.add_done_callback(lambda _t: _tasks.pop(key, None) if _tasks.get(key) is _t else None)
    return t


def _city_task(uni: University, origin: tuple[float, float]) -> asyncio.Task:
    """One city-boundary lookup per university: the client's repeat requests join the one already running."""
    from .citygeo import city_area
    return _shared(("city", uni.qid), lambda: city_area(uni, origin))


def _outline_task(qid: str, names: list[str], lat: float, lon: float) -> asyncio.Task:
    from .sources import osm
    return _shared(("outline", qid), lambda: asyncio.wait_for(osm.nominatim_outline(qid, names, lat, lon), timeout=4))


def warm(qid: str, lat: float, lon: float, names: list[str | None]) -> None:
    """Start the network part of a pack (tiles, campus outline) from the index row while the university's facts are
    still being looked up (Wikidata, 2-5 s): build() then finds them done or joins them."""
    _shared(("warm", qid, round(lat, 4), round(lon, 4)),
            lambda: _tile_blobs(bbox_around(lat, lon, max(DORM_RADIUS_M, POI_RADIUS_M))), keep=False)
    names = [n for n in dict.fromkeys(names) if n]
    if names:
        _outline_task(qid, names, lat, lon)


# ---------------------------------------------------------------- tiles

async def _fetch_template() -> str:
    global _tile_tpl
    hit = await cache.kv_get("map3d_tiles", "template", max_age_s=6 * 3600)  # survives restarts: ~1-4 s saved
    if hit:
        _tile_tpl = (hit["url"], time.time())
        return hit["url"]
    d = await http.get_json(TILEJSON, timeout=8.0)
    _tile_tpl = (d["tiles"][0], time.time())
    await cache.kv_set("map3d_tiles", "template", {"url": _tile_tpl[0]})
    return _tile_tpl[0]


async def _template() -> str:
    if _tile_tpl and time.time() - _tile_tpl[1] < 6 * 3600:
        return _tile_tpl[0]
    # all tiles of a pack ask at once: one TileJSON request, not sixteen
    return await asyncio.shield(_shared("template", _fetch_template, keep=False))


def _tile_xy(lat: float, lon: float, z: int = Z) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _tiles_for_bbox(bb: list[float]) -> list[tuple[int, int]]:
    x0, y0 = _tile_xy(bb[2], bb[1])   # top-left: max lat, min lon
    x1, y1 = _tile_xy(bb[0], bb[3])   # bottom-right: min lat, max lon
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def _tile_dir() -> Path:
    d = settings.data_dir / "tiles" / str(Z)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _tile_bytes(x: int, y: int) -> bytes | None:
    return await asyncio.shield(_shared(("tile", x, y), lambda: _fetch_tile(x, y), keep=False))


async def _fetch_tile(x: int, y: int) -> bytes | None:
    path = _tile_dir() / f"{x}_{y}.pbf"
    if path.exists() and time.time() - path.stat().st_mtime < TILE_TTL_S:
        return path.read_bytes()
    try:
        url = (await _template()).format(z=Z, x=x, y=y)
        r = await http.get(url, timeout=8.0, allow_insecure=False)
        if r.status_code == 204:
            data = b""
        elif r.status_code != 200:
            log.info("tile %s/%s -> %s", x, y, r.status_code)
            return None
        else:
            data = r.content
        path.write_bytes(data)
        return data
    except Exception as e:  # noqa: BLE001
        log.info("tile %s/%s failed: %s", x, y, type(e).__name__)
        return path.read_bytes() if path.exists() else None


class Tile:
    """One z14 tile with coordinates converted to lon/lat. Layers are decoded on first use, one at a time: parsing the
    protobuf is native and cheap, the geometry decode is pure Python (~2 s for a dense city pack), and the building
    layer is needed only where the campus and the dorms are - the POI layer everywhere, roads and the rest never."""

    def __init__(self, x: int, y: int, data: bytes):
        self.x, self.y = x, y
        self._pb = None
        if data:
            self._pb = vector_tile_pb2.tile()
            self._pb.ParseFromString(data)
        self._layers: dict[str, dict | None] = {}
        self._buildings: list[dict] | None = None
        self._tree: STRtree | None = None
        (x0, y0), (x1, y1) = self._lonlat(0, 0, 1), self._lonlat(1, 1, 1)
        self.bounds = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def query(self, geom) -> list[dict]:
        """Buildings whose bounding box meets `geom` (parses and indexes the tile on first use)."""
        if not self.bounds.intersects(geom):
            return []
        items = self.buildings()
        if self._tree is None:
            self._tree = STRtree([b["shape"] for b in items])
        return [items[i] for i in self._tree.query(geom)]

    def _pb_layer(self, name: str):
        for pb in self._pb.layers if self._pb is not None else []:
            if pb.name == name:
                return pb
        return None

    def layer(self, name: str) -> dict | None:
        if name not in self._layers:
            pb = self._pb_layer(name)
            found = None
            if pb is not None:
                one = vector_tile_pb2.tile()
                one.layers.add().CopyFrom(pb)
                found = mapbox_vector_tile.decode(one.SerializeToString(), default_options={"y_coord_down": True}).get(name)
            self._layers[name] = found
        return self._layers[name]

    def _lonlat(self, px: float, py: float, extent: int) -> tuple[float, float]:
        n = 2 ** Z
        lon = (self.x + px / extent) / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (self.y + py / extent) / n))))
        return lon, lat

    def buildings(self) -> list[dict]:
        if self._buildings is not None:
            return self._buildings
        layer = self.layer("building")
        if not layer:
            self._buildings = []
            return []
        ext = layer.get("extent", 4096)
        clip = box(0, 0, ext, ext)
        out = []
        for fi, f in enumerate(layer["features"]):
            props = f.get("properties") or {}
            g = f.get("geometry") or {}
            if props.get("hide_3d") or g.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            # a feature groups many buildings of the same height: handle each outer ring on its own (cheap)
            parts = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for pi, rings in enumerate(parts):
                if not rings or len(rings[0]) < 4:
                    continue
                xs = [c[0] for c in rings[0]]
                ys = [c[1] for c in rings[0]]
                if max(xs) <= 0 or min(xs) >= ext or max(ys) <= 0 or min(ys) >= ext:
                    continue  # lies in the tile buffer: the neighbour tile owns it
                try:
                    poly = Polygon(rings[0])
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if min(xs) < 0 or min(ys) < 0 or max(xs) > ext or max(ys) > ext:
                        poly = poly.intersection(clip)
                    if not isinstance(poly, Polygon):
                        poly = max((p for p in getattr(poly, "geoms", []) if isinstance(p, Polygon)), key=lambda p: p.area, default=None)
                except Exception:  # noqa: BLE001
                    continue
                if poly is None or poly.is_empty or poly.area < 4:
                    continue
                ring = [self._lonlat(px, py, ext) for px, py in poly.exterior.coords]
                out.append({
                    "id": f"{self.x}.{self.y}.{fi}.{pi}",
                    "shape": Polygon(ring),
                    "h": float(props.get("render_height") or 0) or None,
                    "min_h": float(props.get("render_min_height") or 0),
                })
        self._buildings = out
        return out

    def pois(self) -> list[dict]:
        """Single points of the POI layer, read straight from the protobuf: a city-centre tile holds ~1 000 places with
        dozens of name:xx tags each, and the generic decoder converted every tag of every place (~3 s for London)."""
        pb = self._pb_layer("poi")
        if pb is None:
            return []
        ext = pb.extent or 4096
        keys = list(pb.keys)
        wanted = [k in ("class", "subclass", "rank") or k.startswith("name") for k in keys]
        values = pb.values
        memo: dict[int, object] = {}

        def value(i: int):
            if i not in memo:
                fields = values[i].ListFields()
                memo[i] = fields[0][1] if fields else None
            return memo[i]

        out = []
        for f in pb.features:
            g = f.geometry
            if f.type != 1 or len(g) != 3 or g[0] != 9:  # one MoveTo of one point; multipoints are skipped as before
                continue
            px, py = (g[1] >> 1) ^ -(g[1] & 1), (g[2] >> 1) ^ -(g[2] & 1)  # zigzag
            if not (0 <= px <= ext and 0 <= py <= ext):  # tile buffer: the neighbour tile owns it
                continue
            tags = f.tags
            p = {keys[tags[i]]: value(tags[i + 1]) for i in range(0, len(tags) - 1, 2) if wanted[tags[i]]}
            lon, lat = self._lonlat(px, py, ext)
            out.append({"lat": lat, "lon": lon, "class": p.get("class"), "subclass": p.get("subclass"),
                        "rank": p.get("rank"), "names": {k: v for k, v in p.items() if k.startswith("name")}})
        return out


async def _tile_blobs(bb: list[float]) -> list[tuple[tuple[int, int], bytes]]:
    xy = _tiles_for_bbox(bb)[:64]
    datas = await asyncio.gather(*(_tile_bytes(x, y) for x, y in xy))
    return [(k, d) for k, d in zip(xy, datas) if d is not None]


# ---------------------------------------------------------------- helpers

def _m_per_deg(lat: float) -> tuple[float, float]:
    return 111_320.0, 111_320.0 * max(math.cos(math.radians(lat)), 0.05)


def _area_m2(poly: Polygon, lat: float) -> float:
    my, mx = _m_per_deg(lat)
    return poly.area * mx * my


def _ring(poly: Polygon) -> list[list[float]]:
    return [[round(lat, 6), round(lon, 6)] for lon, lat in poly.exterior.coords]


def _poi_name(names: dict, lang: str = "ru") -> str | None:
    for k in (f"name:{lang}", "name", "name_en", "name:en", "name_int"):
        if names.get(k):
            return names[k]
    return None


def _group_of(p: dict) -> str | None:
    cls, sub = p.get("class"), p.get("subclass")
    if sub == "dormitory":
        return "dorm"
    for g, rule in GROUPS.items():
        if sub in rule["subclass"] or (cls in rule["class"] and sub not in {"parking"}):
            return g
    return None


def _building_at(tiles: list[Tile], lat: float, lon: float, max_m: float = 30.0) -> dict | None:
    """The footprint containing the point, else the nearest one within max_m."""
    pt = Point(lon, lat)
    my, mx = _m_per_deg(lat)
    area = pt.buffer(max_m / mx)
    best, best_d = None, None
    for t in tiles:
        for b in t.query(area):
            s = b["shape"]
            if s.contains(pt):
                return b
            d = s.distance(pt) * (mx + my) / 2
            if d <= max_m and (best_d is None or d < best_d):
                best, best_d = b, d
    return best


_topo_lock = asyncio.Lock()
_topo_last = 0.0
ELEV_KV_MAX = 40   # requests up to this many points are also remembered on disk, point by point (anchors, dorms)


async def _open_topo(chunk: list[tuple[float, float]]) -> dict[tuple[float, float], float]:
    """OpenTopoData (SRTM 90 m, ASTER 30 m past SRTM's 60 N): the fallback when Open-Meteo refuses (it has an hourly
    limit). Public API: at most 1 request a second and 100 points a request."""
    global _topo_last
    out: dict[tuple[float, float], float] = {}
    for i in range(0, len(chunk), 100):
        part = chunk[i:i + 100]
        async with _topo_lock:
            wait = 1.05 - (time.monotonic() - _topo_last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                d = await http.get_json("https://api.opentopodata.org/v1/srtm90m,aster30m",
                                        params={"locations": "|".join(f"{k[0]},{k[1]}" for k in part)}, timeout=8.0)
            except Exception as e:  # noqa: BLE001
                log.info("opentopodata failed: %s", type(e).__name__)
                d = {}
            finally:
                _topo_last = time.monotonic()
        for k, r in zip(part, d.get("results") or []):
            e = r.get("elevation")
            if isinstance(e, (int, float)) and not math.isnan(e):
                out[k] = float(e)
    return out


async def elevations(points: list[tuple[float, float]]) -> list[float | None]:
    """Ground height above sea level (m) for each (lat, lon): memoised, then Open-Meteo, then OpenTopoData for what
    it did not answer. A missing height is not harmless: the 3D camera's target altitude comes from it, and at 0 m the
    camera sat inside the terrain of any city high up (Osh ~1 000 m, Karakol ~1 760 m)."""
    keys = [(round(a, 4), round(b, 4)) for a, b in points]
    miss = [k for k in dict.fromkeys(keys) if k not in _elev_memo]
    small = len(miss) <= ELEV_KV_MAX
    if miss and small:   # remembered on disk from an earlier run
        got = await asyncio.gather(*(cache.kv_get("elev", f"{k[0]},{k[1]}") for k in miss))
        for k, v in zip(miss, got):
            if v is not None:
                _elev_memo[k] = float(v["m"])
        miss = [k for k in miss if k not in _elev_memo]
    fresh: dict[tuple[float, float], float] = {}
    for i in range(0, len(miss), 90):
        chunk = miss[i:i + 90]
        params = {"latitude": ",".join(str(k[0]) for k in chunk), "longitude": ",".join(str(k[1]) for k in chunk)}
        for attempt in range(2):
            try:
                d = await http.get_json("https://api.open-meteo.com/v1/elevation", params=params, timeout=6.0)
                for k, e in zip(chunk, d.get("elevation") or []):
                    if e is not None and not (isinstance(e, float) and math.isnan(e)):
                        fresh[k] = float(e)
                break
            except Exception as e:  # noqa: BLE001
                log.info("elevation failed (%s): %s", attempt, type(e).__name__)
                if attempt == 0:
                    await asyncio.sleep(0.5)
    rest = [k for k in miss if k not in fresh]
    if rest:
        fresh.update(await _open_topo(rest))
    _elev_memo.update(fresh)
    if fresh and small:
        try:  # one writer at a time: the database is shared with the profile builds
            for k, v in fresh.items():
                await cache.kv_set("elev", f"{k[0]},{k[1]}", {"m": v})
        except Exception as e:  # noqa: BLE001 - a cache that did not save is not an error
            log.info("elevation cache write failed: %s", type(e).__name__)
    return [_elev_memo.get(k) for k in keys]


def _feature(b: dict, kind: str, lat0: float, lon0: float, **extra) -> dict:
    c = b["shape"].centroid
    return {
        "id": b["id"], "kind": kind, "ring": _ring(b["shape"]),
        "height": round(b["h"], 1) if b["h"] else None, "min_height": round(b["min_h"], 1),
        "lat": round(c.y, 6), "lon": round(c.x, 6),
        "area_m2": round(_area_m2(b["shape"], c.y)),
        "distance_m": round(haversine_km(lat0, lon0, c.y, c.x) * 1000), **extra,
    }


def _matches_uni(name: str | None, uni: University) -> bool:
    if not name:
        return False
    names = [n for n in [uni.name, *uni.names.values(), *uni.aliases] if n and len(n) >= 3]
    low = name.lower()
    for n in names:
        if len(n) <= 6:  # abbreviation (КазНУ, KBTU, ENU): whole word only
            if sum(ch.isupper() for ch in n) >= 2 and re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", low):
                return True
            continue
        if fuzz.token_set_ratio(n, name) >= 80 or fuzz.partial_ratio(n.lower(), low) >= 90:
            return True
    return False


# ---------------------------------------------------------------- pack

def _area_shape(ring_latlon: list[list[float]]) -> Polygon | MultiPolygon | None:
    """make_valid may return a GeometryCollection: keep only the areal parts."""
    g = make_valid(Polygon([(lon, lat) for lat, lon in ring_latlon]))
    if isinstance(g, (Polygon, MultiPolygon)):
        return g if not g.is_empty else None
    polys = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and not p.is_empty]
    return MultiPolygon(polys) if len(polys) > 1 else (polys[0] if polys else None)


async def _campus_outline(uni: University, campus: Campus | None) -> tuple[Polygon | MultiPolygon | None, str | None]:
    if campus and campus.polygon and len(campus.polygon) >= 4:
        return _area_shape(campus.polygon), campus.osm_url
    names = [n for n in [uni.names.get("ru"), uni.names.get("en"), uni.name, *uni.names.values()] if n]
    try:  # usually started already by warm(), from the index row
        c = await asyncio.shield(_outline_task(uni.qid, list(dict.fromkeys(names)), uni.lat, uni.lon))
    except Exception:  # noqa: BLE001
        c = None
    if c and c.polygon:
        return _area_shape(c.polygon), c.osm_url
    return None, None


def _analyse(uni: University, outline, blobs: list, lang: str, origin: tuple[float, float],
             anchor: tuple[float, float]) -> tuple[list[dict], list[dict], dict[str, list[dict]], dict]:
    """CPU part of the pack (runs in a worker thread): campus buildings, dormitories, places."""
    lat0, lon0 = origin
    anchor_lat, anchor_lon = anchor
    tiles = [Tile(x, y, d) for (x, y), d in blobs]
    pois = [p for t in tiles for p in t.pois()]

    # campus buildings: mostly inside the outline, plus the buildings under the university's own points
    by_id: dict[str, dict] = {}
    if outline is not None:
        grown = outline.buffer(0.00005)
        for t in tiles:
            for b in t.query(grown):
                s = b["shape"]
                try:
                    if s.intersection(grown).area / max(s.area, 1e-12) >= 0.5:
                        by_id[b["id"]] = b
                except Exception:  # noqa: BLE001
                    continue
    uni_pois = [p for p in pois if p["class"] == "college" and _matches_uni(_poi_name(p["names"], lang), uni)
                and haversine_km(anchor_lat, anchor_lon, p["lat"], p["lon"]) < 2.0]
    for la, lo in [(lat0, lon0)] + [(p["lat"], p["lon"]) for p in uni_pois]:
        b = _building_at(tiles, la, lo, max_m=25)
        if b:
            by_id[b["id"]] = b
    main_b = _building_at(tiles, lat0, lon0, max_m=60)
    if main_b and outline is None:
        by_id[main_b["id"]] = main_b
    campus_feats = [_feature(b, "campus", anchor_lat, anchor_lon, main=bool(main_b and b["id"] == main_b["id"]))
                    for b in sorted(by_id.values(), key=lambda b: -b["shape"].area)[:MAX_CAMPUS_BUILDINGS]]

    # dormitories: tagged dormitory POIs or dorm-like names within DORM_RADIUS_M
    dorms: list[dict] = []
    seen_dorm_buildings: set[str] = set()
    for p in pois:
        name = _poi_name(p["names"], lang)
        is_dorm = p["subclass"] == "dormitory" or (p["class"] in ("lodging", "college") and name and DORM_NAME_RE.search(name))
        if not is_dorm:
            continue
        d_m = haversine_km(anchor_lat, anchor_lon, p["lat"], p["lon"]) * 1000
        if d_m > DORM_RADIUS_M:
            continue
        inside = bool(outline is not None and outline.contains(Point(p["lon"], p["lat"])))
        b = _building_at(tiles, p["lat"], p["lon"], max_m=30)
        if b and b["id"] in seen_dorm_buildings:
            continue
        own = "campus" if inside else ("name" if _matches_uni(name, uni) else "unknown")
        item = {"id": f"osm:{round(p['lat'], 5)},{round(p['lon'], 5)}", "name": name, "lat": round(p["lat"], 6),
                "lon": round(p["lon"], 6), "distance_m": round(d_m), "ownership": own, "source": "osm"}
        if b:
            seen_dorm_buildings.add(b["id"])
            item["building"] = _feature(b, "dorm", anchor_lat, anchor_lon)
            campus_feats = [f for f in campus_feats if f["id"] != b["id"]]
        dorms.append(item)
    dorms.sort(key=lambda d: ({"campus": 0, "name": 1, "unknown": 2}[d["ownership"]], d["distance_m"]))

    # nearby places from OSM (fallback when Google Places is unavailable on the client)
    places: dict[str, list[dict]] = {}
    for p in pois:
        g = _group_of(p)
        if not g or g == "dorm":
            continue
        name = _poi_name(p["names"], lang)
        if not name and g not in ("transit",):
            continue
        d_m = haversine_km(anchor_lat, anchor_lon, p["lat"], p["lon"]) * 1000
        if d_m > POI_RADIUS_M:
            continue
        places.setdefault(g, []).append({"id": f"osm:{round(p['lat'], 5)},{round(p['lon'], 5)}", "name": name,
                                         "type": p["subclass"] or p["class"], "lat": round(p["lat"], 6),
                                         "lon": round(p["lon"], 6), "distance_m": round(d_m), "source": "osm"})
    for g in places:
        places[g] = sorted(places[g], key=lambda x: x["distance_m"])[:40]
    stats = {"tiles": len(tiles), "tiles_parsed": sum(t._buildings is not None for t in tiles), "pois_scanned": len(pois)}
    return campus_feats, dorms, places, stats


async def build(uni: University, campus: Campus | None, photos: list[dict] | None = None, lang: str = "ru") -> dict:
    if uni.lat is None or uni.lon is None:
        raise ValueError("no coordinates")
    lat0, lon0 = uni.lat, uni.lon
    radius = max(DORM_RADIUS_M, POI_RADIUS_M)
    t0 = time.monotonic()
    took: dict[str, float] = {}  # step -> seconds since the start, for one log line per pack
    # the tiles around the university download while the outline and the city boundary are looked up
    first_tiles = asyncio.create_task(_tile_blobs(bbox_around(lat0, lon0, radius)))
    city_task = _city_task(uni, (lat0, lon0))
    outline, osm_url = await _campus_outline(uni, campus)
    took["outline"] = time.monotonic() - t0
    if outline is not None and (outline.is_empty or _area_m2(outline, lat0) > 25_000_000):
        outline = None  # a whole district tagged as the university is useless as a campus outline
    if outline is not None:
        c = outline.representative_point() if not outline.contains(Point(lon0, lat0)) else Point(lon0, lat0)
        if haversine_km(lat0, lon0, c.y, c.x) > 3:
            outline = None  # outline of a different campus far away
    anchor_lat, anchor_lon = (lat0, lon0)
    if outline is not None and not outline.contains(Point(lon0, lat0)):
        rp = outline.representative_point()
        anchor_lat, anchor_lon = rp.y, rp.x

    bb = bbox_around(anchor_lat, anchor_lon, radius)
    if outline is not None:
        minx, miny, maxx, maxy = outline.bounds
        bb = [min(bb[0], miny), min(bb[1], minx), max(bb[2], maxy), max(bb[3], maxx)]
    blobs = await first_tiles
    have = {k for k, _ in blobs}
    if any(k not in have for k in _tiles_for_bbox(bb)):  # a moved anchor or a large outline needs a few more tiles
        blobs = [b for b in await _tile_blobs(bb)]
    took["tiles"] = time.monotonic() - t0
    campus_feats, dorms, places, stats = await _in_pool(
        _analyse, uni, outline, blobs, lang, (lat0, lon0), (anchor_lat, anchor_lon))
    took["analyse"] = time.monotonic() - t0

    center = None
    if uni.city_lat is not None and uni.city_lon is not None:
        center = {"name": uni.city, "lat": uni.city_lat, "lon": uni.city_lon, "population": uni.city_population,
                  "distance_km": round(haversine_km(anchor_lat, anchor_lon, uni.city_lat, uni.city_lon), 2)}
    ctx = await cache.kv_get("context", uni.qid)
    route = (ctx or {}).get("route_center") if ctx else None

    elev_pts = [(anchor_lat, anchor_lon)] + ([(center["lat"], center["lon"])] if center else []) + [(d["lat"], d["lon"]) for d in dorms]
    # the city boundary's last chance runs alongside the elevation request, not after it
    city_wait = asyncio.ensure_future(asyncio.wait_for(asyncio.shield(city_task), timeout=CITY_WAIT_S))
    elev = await elevations(elev_pts)
    took["elevation"] = time.monotonic() - t0
    if center:
        center["elevation"] = elev[1]
    for d, e in zip(dorms, elev[1 + (1 if center else 0):]):
        d["elevation"] = e

    geo_photos = []
    for ph in photos or []:
        if ph.get("lat") is None or ph.get("lon") is None or ph.get("rejected"):
            continue
        d_m = haversine_km(anchor_lat, anchor_lon, ph["lat"], ph["lon"]) * 1000
        if d_m > 3000:
            continue
        geo_photos.append({"id": ph["id"], "lat": ph["lat"], "lon": ph["lon"], "thumb": ph.get("thumb"),
                           "category": ph.get("category"), "level": ph.get("level"), "page_url": ph.get("page_url"),
                           "source_label": ph.get("source_label"), "distance_m": round(d_m)})

    city_status = "ok"
    try:
        # shield: a slow Nominatim keeps working in the background; the client's next request picks up its result
        city = await city_wait
        if city is None:
            city_status = "none"
    except Exception as e:  # noqa: BLE001
        log.info("city area not ready: %s", type(e).__name__)
        city, city_status = None, "pending"
    log.info("map3d %s in %.1f s (%s; city %s, %d tiles %d KB)", uni.qid, time.monotonic() - t0,
             ", ".join(f"{k} {v:.1f}" for k, v in took.items()), city_status, len(blobs), sum(len(b) for _, b in blobs) // 1024)

    return {
        "v": PACK_VERSION,
        "university": {"qid": uni.qid, "name": uni.name, "names": uni.names, "aliases": uni.aliases[:12], "city": uni.city,
                       "country": uni.country, "website": uni.website, "lat": lat0, "lon": lon0},
        "anchor": {"lat": round(anchor_lat, 6), "lon": round(anchor_lon, 6), "elevation": elev[0]},
        "campus": {"mode": "polygon" if outline is not None else "radius",
                   "outline": _ring(outline) if isinstance(outline, Polygon) else (
                       _ring(max(outline.geoms, key=lambda g: g.area)) if isinstance(outline, MultiPolygon) else None),
                   "area_ha": round(_area_m2(outline, lat0) / 10_000, 1) if outline is not None else None,
                   "osm_url": osm_url, "buildings": campus_feats},
        "dorms": dorms[:30],
        "places": places,
        "center": center,
        "city_area": city,
        "city_status": city_status,
        "route_center": route,
        "photos": geo_photos[:60],
        "stats": stats,
        "sources": [
            {"label": "OpenStreetMap (OpenFreeMap tiles)", "url": osm_url or "https://www.openstreetmap.org/"},
            {"label": "Open-Meteo Elevation (Copernicus DEM)", "url": "https://open-meteo.com/en/docs/elevation-api"},
        ],
    }


async def footprints(points: list[dict]) -> dict[str, dict | None]:
    """Building footprint under each point ({id, lat, lon}), e.g. dormitories found by Google Places."""
    points = [p for p in points if isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float))][:40]
    if not points:
        return {}
    xy = list({_tile_xy(p["lat"], p["lon"]) for p in points})
    datas = await asyncio.gather(*(_tile_bytes(x, y) for x, y in xy))
    elev = await elevations([(p["lat"], p["lon"]) for p in points])

    def run() -> dict[str, dict | None]:
        tiles = [Tile(k[0], k[1], d) for k, d in zip(xy, datas) if d is not None]
        out: dict[str, dict | None] = {}
        for p, e in zip(points, elev):
            b = _building_at(tiles, p["lat"], p["lon"], max_m=35)
            out[str(p.get("id"))] = {**_feature(b, "dorm", p["lat"], p["lon"]), "elevation": e} if b else {"elevation": e, "ring": None}
        return out
    return await _in_pool(run)
