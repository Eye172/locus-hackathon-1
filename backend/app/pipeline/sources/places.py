"""Google Places (New): the photos Google Maps shows for the university's pins (requires GOOGLE_MAPS_API_KEY).

Not one pin but the campus as Maps knows it: the university itself, and - theme by theme from the search plan - its
dorms, library, sports complex, canteen as their own places near the campus ("<name> dormitory" etc.). Every place
has up to ten photos, most of them uploaded by visitors and students, some by the university; each carries its
author's attribution, which is shown with the photo. A place counts when its name contains the university's name
(or, right at the campus pin, when it is the kind of place asked for) - the same rule as the review photos of
sources/map_reviews.py, so a downtown campus does not collect every canteen and other university's dorm around it.

Cost (Places API New, billed to the key's project, with a monthly free allowance per SKU): one Text Search per
query and one photo-media call per photo. Answers are cached for a month (bucket "gplaces"), so a rebuild is free.
"""
from __future__ import annotations

import asyncio
import logging
import re

from ... import cache, http
from ...config import settings
from ...geo import haversine_km
from ...models import PhotoCandidate, University

log = logging.getLogger("campuslens.places")

SEARCH = "https://places.googleapis.com/v1/places:searchText"
FIELDS = "places.id,places.displayName,places.photos,places.googleMapsUri,places.location,places.types,places.primaryType"
TTL = 30 * 86400
MAIN_PHOTOS = 10          # the university's own pin
THEME_PHOTOS = 6          # each dorm / library / canteen pin
PER_THEME_PLACES = 2
# Google place types that belong to a theme (the query word itself is checked too)
THEME_TYPES = {"dorm": ("lodging", "dormitory", "student_housing", "housing", "residence", "hostel"),
               "library": ("library",), "sports": ("gym", "sports", "stadium", "swimming", "fitness", "athletic"),
               "food": ("restaurant", "cafe", "food", "cafeteria", "canteen", "coffee", "dining")}
OTHER_UNI = re.compile(r"университет|university|институт|institute|академи|academy|college|колледж", re.I)


async def _search(query: str, lat: float | None, lon: float | None, radius: float, n: int) -> list[dict]:
    key = f"{query}@{lat and round(lat, 3)},{lon and round(lon, 3)},{int(radius)}"
    hit = await cache.kv_get("gplaces", key, max_age_s=TTL)
    if hit is not None:
        return hit
    body: dict = {"textQuery": query, "maxResultCount": n}
    if lat is not None and lon is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius}}
    try:
        r = await http.post(SEARCH, json=body, timeout=8.0, headers={
            "X-Goog-Api-Key": settings.google_maps_api_key, "X-Goog-FieldMask": FIELDS})
        if r.status_code != 200:
            log.warning("places search %r -> %s %s", query, r.status_code, r.text[:160])
            return []
        places = r.json().get("places") or []
    except Exception as e:  # noqa: BLE001
        log.warning("places search %r failed: %r", query, e)
        return []
    await cache.kv_set("gplaces", key, places)
    return places


async def _photo_uri(name: str) -> str | None:
    hit = await cache.kv_get("gplaces", f"photo:{name}", max_age_s=TTL)
    if hit:
        return hit.get("uri")
    try:
        d = await http.get_json(f"https://places.googleapis.com/v1/{name}/media", params={
            "maxWidthPx": 1600, "skipHttpRedirect": "true", "key": settings.google_maps_api_key,
        }, timeout=6.0)
        uri = d.get("photoUri")
    except Exception:  # noqa: BLE001
        return None
    if uri:
        await cache.kv_set("gplaces", f"photo:{name}", {"uri": uri})
    return uri


async def _photos(place: dict, limit: int, intent: str | None, query: str) -> list[PhotoCandidate]:
    photos = (place.get("photos") or [])[:limit]
    uris = await asyncio.gather(*[_photo_uri(p["name"]) for p in photos])
    title = (place.get("displayName") or {}).get("text") or ""
    out: list[PhotoCandidate] = []
    for p, uri in zip(photos, uris):
        if not uri:
            continue
        attr = (p.get("authorAttributions") or [{}])[0]
        out.append(PhotoCandidate(
            url=uri, page_url=place.get("googleMapsUri") or "https://maps.google.com",
            source="places", title=title, text=title, author=attr.get("displayName"),
            license=f"Фото с Google Карт © {attr.get('displayName') or 'автор'}",
            width=p.get("widthPx"), height=p.get("heightPx"), collector=f"gplaces:{place.get('id')}",
            intent=intent, query=query,
        ))
    return out


async def collect(uni: University, plan=None) -> list[PhotoCandidate]:
    if not settings.google_maps_api_key:
        return []
    from .. import search_plan as sp
    from .web_images import CIS, _short_name, country_code
    en = uni.names.get("en") or uni.name
    main = await _search(f"{en} {uni.city or ''}".strip(), uni.lat, uni.lon, 3000.0, 1)
    out: list[PhotoCandidate] = []
    seen: set[str] = set()
    if main:
        seen.add(main[0]["id"])
        out += await _photos(main[0], MAIN_PHOTOS, None, en)
    if plan is None or uni.lat is None:
        return out
    lang = "ru" if country_code(uni) in CIS else "en"
    name = _short_name(uni, lang)
    forms = sp.name_forms(uni)
    jobs: list[tuple[str, str, str]] = []
    for i in sp.enabled(plan, "maps"):
        for word in i.maps:
            jobs.append((i.key, f"{name if word.isascii() == (lang == 'en') else en} {word}", word))
    results = await asyncio.gather(*[_search(q, uni.lat, uni.lon, 2000.0, 6) for _, q, _ in jobs])
    picks: list[tuple[str, str, dict]] = []
    per_theme: dict[str, int] = {}
    for (ik, q, word), places in zip(jobs, results):
        for p in places:
            loc = p.get("location") or {}
            if p.get("id") in seen or loc.get("latitude") is None or per_theme.get(ik, 0) >= PER_THEME_PLACES:
                continue
            title = ((p.get("displayName") or {}).get("text") or "")
            low = title.lower()
            km = haversine_km(uni.lat, uni.lon, loc["latitude"], loc["longitude"])
            kind = f"{low} {' '.join(p.get('types') or [])} {p.get('primaryType') or ''}".lower()
            full = any(f in low for f in forms["full"])
            short = any(re.search(rf"(?<![\w]){re.escape(a)}(?![\w])", low) for a in forms["abbr"])
            wanted = word.lower()[:5] in kind or any(w in kind for w in THEME_TYPES.get(ik, ()))
            named = full or short
            # the full name is enough; a short one ("Pratt") also has to be the kind of place asked for - the Pratt
            # Center for Community Development is not a sports complex; an unnamed pin must be right at the campus
            if km > 2.5 or not (full or (short and wanted) or (km <= 0.35 and wanted)):
                continue
            if not named and OTHER_UNI.search(title):
                continue      # a dorm, but of another university
            seen.add(p["id"])
            per_theme[ik] = per_theme.get(ik, 0) + 1
            picks.append((ik, q, p))
    lists = await asyncio.gather(*[_photos(p, THEME_PHOTOS, ik, q) for ik, q, p in picks])
    for lst in lists:
        out += lst
    log.info("places %s: main=%s themes=%s -> %d photos", uni.qid, bool(main),
             [((p.get('displayName') or {}).get('text'), ik) for ik, _, p in picks], len(out))
    return out
