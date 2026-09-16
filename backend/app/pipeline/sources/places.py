"""Google Places (New): photos attached to the university's place entity (requires GOOGLE_MAPS_API_KEY)."""
from __future__ import annotations

import asyncio

from ... import http
from ...config import settings
from ...models import PhotoCandidate, University

SEARCH = "https://places.googleapis.com/v1/places:searchText"


async def _photo_uri(name: str) -> str | None:
    try:
        d = await http.get_json(f"https://places.googleapis.com/v1/{name}/media", params={
            "maxWidthPx": 800, "skipHttpRedirect": "true", "key": settings.google_maps_api_key,
        }, timeout=5.0)
        return d.get("photoUri")
    except Exception:
        return None


async def collect(uni: University) -> list[PhotoCandidate]:
    if not settings.google_maps_api_key:
        return []
    body = {"textQuery": f"{uni.names.get('en') or uni.name} {uni.city or ''}".strip(), "maxResultCount": 1}
    if uni.lat and uni.lon:
        body["locationBias"] = {"circle": {"center": {"latitude": uni.lat, "longitude": uni.lon}, "radius": 3000.0}}
    try:
        r = await http.post(SEARCH, json=body, headers={
            "X-Goog-Api-Key": settings.google_maps_api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.photos,places.googleMapsUri",
        }, timeout=6.0)
        places = r.json().get("places") or []
    except Exception:
        return []
    if not places:
        return []
    place = places[0]
    photos = place.get("photos") or []
    uris = await asyncio.gather(*[_photo_uri(p["name"]) for p in photos[:10]])
    out: list[PhotoCandidate] = []
    for p, uri in zip(photos, uris):
        if not uri:
            continue
        attr = (p.get("authorAttributions") or [{}])[0]
        out.append(PhotoCandidate(
            url=uri, page_url=place.get("googleMapsUri") or "https://maps.google.com",
            source="places", title=(place.get("displayName") or {}).get("text"),
            text=(place.get("displayName") or {}).get("text", ""),
            author=attr.get("displayName"), license="Google Maps user content",
            width=p.get("widthPx"), height=p.get("heightPx"),
        ))
    return out
