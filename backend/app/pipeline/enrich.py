"""Turn a QID into facts + coordinates (fast) and, separately, the campus outline (slower, Overpass)."""
from __future__ import annotations

import logging

from .. import http
from ..geo import bbox_around
from ..models import Campus, University
from .resolve import index
from .sources import osm, wikidata, wikipedia

log = logging.getLogger("campuslens.enrich")


async def _nominatim(name: str) -> tuple[float, float] | None:
    try:
        d = await http.get_json("https://nominatim.openstreetmap.org/search", params={
            "q": name, "format": "jsonv2", "limit": 1,
        }, timeout=4.0)
        if d:
            return float(d[0]["lat"]), float(d[0]["lon"])
    except Exception:
        return None
    return None


async def facts(qid: str, log_fn=None) -> tuple[University, dict | None]:
    """Wikidata entity + Wikipedia summary + coordinates from the first source that has them."""
    logf = log_fn or (lambda s: None)
    if qid.startswith("W"):  # found in the web, not in Wikidata (see sources/websearch.py)
        from .. import cache as cache_mod
        ent = await cache_mod.kv_get("web", qid)
        if not ent:
            raise RuntimeError("Этот вуз найден в вебе в прошлой сессии, повторите поиск")
        uni = University(qid=qid, name=ent["name"], names={"en": ent["name"]}, website=ent.get("website"),
                         description=ent.get("snippet"), city=ent.get("city"), lat=ent.get("lat"), lon=ent.get("lon"),
                         coord_source=ent.get("coord_source"))
        if uni.lat is None:
            pt = await _nominatim(f"{uni.name} {uni.city or ''}")
            if pt:
                uni.lat, uni.lon, uni.coord_source = pt[0], pt[1], "nominatim"
        if uni.lat is None:
            raise RuntimeError("Не удалось определить координаты университета: уточните город в запросе")
        logf(f"web entity {ent.get('domain')} · coords from {uni.coord_source}")
        return uni, None
    uni = await wikidata.entity(qid)
    row = index.by_id.get(qid)
    if row:
        uni.website = uni.website or row.get("site")
        uni.commons_category = uni.commons_category or row.get("commons")
        uni.city = uni.city or row.get("city")
        if uni.lat is None and row.get("coord"):
            uni.lat, uni.lon = row["coord"]
            uni.coord_source = "index"

    wiki = await wikipedia.best_summary(uni.wikipedia)
    if wiki:
        uni.summary = wiki.get("extract")
        uni.summary_url = wiki.get("url")
        uni.image_url = uni.image_url or wiki.get("thumbnail")
        if uni.lat is None and wiki.get("lat") is not None:
            uni.lat, uni.lon = wiki["lat"], wiki["lon"]
            uni.coord_source = "wikipedia"
    if uni.lat is None:
        pt = await _nominatim(f"{uni.names.get('en') or uni.name} {uni.city or ''}")
        if pt:
            uni.lat, uni.lon = pt
            uni.coord_source = "nominatim"
    if uni.lat is None:
        raise RuntimeError("Не удалось определить координаты университета ни в одном источнике")
    logf(f"coords {uni.lat:.4f},{uni.lon:.4f} from {uni.coord_source}")
    return uni, wiki


def provisional_campus(uni: University) -> Campus:
    return Campus(bbox=bbox_around(uni.lat, uni.lon, 500), mode="radius", radius_m=500.0)


async def campus(uni: University, log_fn=None) -> Campus:
    """Campus outline and tagged buildings from OSM. Adjusts the anchor point to the outline centre."""
    logf = log_fn or (lambda s: None)
    c = await osm.campus(uni.qid, uni.lat, uni.lon, uni.aliases or [uni.name])
    if c.mode == "polygon" and uni.coord_source != "wikidata":
        b = c.bbox
        uni.lat, uni.lon = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        uni.coord_source = f"{uni.coord_source}+osm_center"
    logf(f"campus {c.mode}: {len(c.buildings)} tagged objects, counts={c.counts}")
    return c
