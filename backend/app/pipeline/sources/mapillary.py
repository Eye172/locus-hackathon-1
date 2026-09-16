"""Mapillary street-level imagery inside the campus bounding box (requires MAPILLARY_TOKEN)."""
from __future__ import annotations

from datetime import datetime, timezone

from ... import http
from ...config import settings
from ...geo import bbox_area_deg2
from ...models import PhotoCandidate

API = "https://graph.mapillary.com/images"


async def collect(bbox: list[float], limit: int = 40) -> list[PhotoCandidate]:
    if not settings.mapillary_token:
        return []
    minlat, minlon, maxlat, maxlon = bbox
    if bbox_area_deg2(bbox) >= 0.01:  # API limit: bbox area must be < 0.01 deg^2
        clat, clon = (minlat + maxlat) / 2, (minlon + maxlon) / 2
        minlat, maxlat, minlon, maxlon = clat - 0.04, clat + 0.04, clon - 0.06, clon + 0.06
    try:
        d = await http.get_json(API, params={
            "access_token": settings.mapillary_token,
            "fields": "id,thumb_1024_url,captured_at,geometry,is_pano,compass_angle,sequence",
            "bbox": f"{minlon},{minlat},{maxlon},{maxlat}",
            "limit": limit * 2,
        }, timeout=6.0)
    except Exception:
        return []
    out: list[PhotoCandidate] = []
    seen_seq: dict[str, int] = {}
    for im in d.get("data", []):
        if im.get("is_pano") or not im.get("thumb_1024_url"):
            continue
        seq = im.get("sequence") or ""
        if seen_seq.get(seq, 0) >= 6:  # keep the walk varied: max 6 frames per sequence
            continue
        seen_seq[seq] = seen_seq.get(seq, 0) + 1
        coords = (im.get("geometry") or {}).get("coordinates") or [None, None]
        date = None
        if im.get("captured_at"):
            date = datetime.fromtimestamp(im["captured_at"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append(PhotoCandidate(
            url=im["thumb_1024_url"],
            page_url=f"https://www.mapillary.com/app/?pKey={im['id']}&focus=photo",
            source="mapillary", title="Mapillary street-level image",
            author="Mapillary contributors", license="CC BY-SA 4.0",
            date=date, date_source="captured",
            lat=coords[1], lon=coords[0],
        ))
        if len(out) >= limit:
            break
    return out
