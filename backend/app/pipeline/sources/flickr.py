"""Flickr geotagged community photos inside the campus bounding box (requires FLICKR_API_KEY)."""
from __future__ import annotations

from ... import http
from ...config import settings
from ...models import PhotoCandidate

API = "https://www.flickr.com/services/rest/"
LICENSES = {
    "0": "All rights reserved", "1": "CC BY-NC-SA 2.0", "2": "CC BY-NC 2.0", "3": "CC BY-NC-ND 2.0",
    "4": "CC BY 2.0", "5": "CC BY-SA 2.0", "6": "CC BY-ND 2.0", "7": "No known copyright restrictions",
    "8": "US Government work", "9": "CC0", "10": "Public Domain Mark",
}


async def collect(bbox: list[float], limit: int = 40) -> list[PhotoCandidate]:
    if not settings.flickr_api_key:
        return []
    minlat, minlon, maxlat, maxlon = bbox
    try:
        d = await http.get_json(API, params={
            "method": "flickr.photos.search", "api_key": settings.flickr_api_key,
            "bbox": f"{minlon},{minlat},{maxlon},{maxlat}", "has_geo": 1, "accuracy": 12,
            "content_types": 0, "safe_search": 1, "min_taken_date": "2008-01-01",
            "extras": "date_taken,owner_name,license,geo,url_c,url_m,url_l,tags,description",
            "per_page": limit, "sort": "interestingness-desc", "format": "json", "nojsoncallback": 1,
        }, timeout=6.0)
    except Exception:
        return []
    out: list[PhotoCandidate] = []
    for p in (d.get("photos") or {}).get("photo", []):
        url = p.get("url_c") or p.get("url_m") or p.get("url_l")
        if not url:
            continue
        desc = (p.get("description") or {}).get("_content", "")
        out.append(PhotoCandidate(
            url=url,
            page_url=f"https://www.flickr.com/photos/{p.get('owner')}/{p.get('id')}",
            source="flickr", title=p.get("title") or None,
            text=" ".join(x for x in [p.get("title"), desc, p.get("tags")] if x),
            author=p.get("ownername"), license=LICENSES.get(str(p.get("license")), "Flickr"),
            date=(p.get("datetaken") or "")[:10] or None, date_source="taken",
            lat=float(p["latitude"]) if p.get("latitude") not in (None, 0, "0") else None,
            lon=float(p["longitude"]) if p.get("longitude") not in (None, 0, "0") else None,
        ))
    return out
