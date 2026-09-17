"""Photos that ordinary visitors attached to reviews of the university on Google Maps (through serper.dev).

Why this source exists: every other search source is anchored by *text* ("<name> campus"), which is how city
buildings and stock renders creep in. A review photo is anchored by *place id* - the pin of the university itself -
so the candidate is about the right organisation before anyone looks at the pixels. What people upload there is the
ordinary side of a campus: corridors, the canteen, a queue at the admissions office, a graduation crowd.

Cost: 1 serper credit for the place lookup (cached in kv forever, key = qid) + 1 credit per page of 20 reviews.
A page carries anywhere between 0 and ~50 photos, so the second page is only fetched when the first was thin.

The photo itself carries no EXIF geotag, so no lat/lon is set: the trust comes from the place id, not from geometry,
and verify.py must not be told otherwise.
"""
from __future__ import annotations

import logging
import re

from ... import cache, http
from ...config import settings
from ...models import PhotoCandidate, University
from ...geo import haversine_km
from .web_images import CIS, _short_name, country_code

log = logging.getLogger("campuslens.map_reviews")

MAPS = "https://google.serper.dev/maps"
REVIEWS = "https://google.serper.dev/reviews"
UNI_TYPE = re.compile(r"universit|univerzit|college|institut|академ|университ|инстит|колледж|вуз|мектеп|jogar", re.I)
MAX_PLACE_KM = 8.0        # the pin may sit on another campus building, but not in another city
CITY_KM = 25.0            # no campus coordinates (a third of the Kazakh rows): the city centre is the only anchor
WANT_PHOTOS = 12          # enough for one album rail; below this the second review page is worth a credit
PER_REVIEW = 4            # one visitor often uploads ten shots of the same hall: keep the album from being their album


def _hi(url: str, w: int = 1600, h: int = 1200) -> str:
    """lh3 review photos arrive as 384x512 (suffix '=k-no'); asking for a size gives the original (~900x1200)."""
    return re.sub(r"=[\w-]+$", "", url) + f"=w{w}-h{h}-k-no"


async def _post(url: str, body: dict) -> dict:
    r = await http.post(url, json=body, timeout=8.0, headers={
        "X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


async def place(uni: University) -> dict | None:
    """The university's Google Maps place id, resolved once per university and cached (None is cached too)."""
    hit = await cache.kv_get("place", uni.qid)
    if hit is not None:
        return hit or None
    cc = country_code(uni)
    lang = "ru" if cc in CIS else "en"
    # the campus pin is the reference point; without it the city centre anchors the search, with a wider radius
    ref = (uni.lat, uni.lon) if uni.lat is not None else (uni.city_lat, uni.city_lon)
    max_km = MAX_PLACE_KM if uni.lat is not None else CITY_KM
    q = _short_name(uni, lang)
    if uni.lat is None and uni.city:
        q = f"{q} {uni.city}"
    body = {"q": q, "hl": lang}
    if ref[0] is not None:
        body["ll"] = f"@{ref[0]},{ref[1]},{14 if uni.lat is not None else 12}z"
    if cc:
        body["gl"] = cc.lower()
    try:
        places = (await _post(MAPS, body)).get("places") or []
    except Exception as e:  # noqa: BLE001
        log.warning("serper maps %s failed: %r", uni.qid, e)
        return None

    best = None
    for p in places[:5]:
        pid, lat, lon = p.get("placeId"), p.get("latitude"), p.get("longitude")
        if not pid or lat is None or lon is None:
            continue
        km = haversine_km(ref[0], ref[1], lat, lon) if ref[0] is not None else None
        if km is not None and km > max_km:
            continue
        title, types = p.get("title") or "", " ".join(p.get("types") or [])
        if not UNI_TYPE.search(title + " " + types):
            continue
        best = {"placeId": pid, "title": title, "lat": lat, "lon": lon,
                "km": round(km, 2) if km is not None else None,
                "reviews": p.get("ratingCount"), "hl": lang, "gl": (cc or "").lower()}
        break
    await cache.kv_set("place", uni.qid, best or {})
    if best:
        log.info("place for %s: %s (%s km, %s reviews)", uni.qid, best["title"], best["km"], best["reviews"])
    return best


async def collect(uni: University, limit: int = 40) -> list[PhotoCandidate]:
    if not settings.serper_api_key:
        return []
    pl = await place(uni)
    if not pl:
        return []
    out: list[PhotoCandidate] = []
    seen: set[str] = set()
    token: str | None = None
    for _ in range(2):
        body = {"placeId": pl["placeId"], "hl": pl["hl"]}
        if pl["gl"]:
            body["gl"] = pl["gl"]
        if token:
            body["nextPageToken"] = token
        try:
            d = await _post(REVIEWS, body)
        except Exception as e:  # noqa: BLE001
            log.warning("serper reviews %s failed: %r", uni.qid, e)
            break
        for r in d.get("reviews") or []:
            author = (r.get("user") or {}).get("name")
            date = (r.get("isoDate") or "")[:10] or None
            snippet = re.sub(r"\s+", " ", r.get("snippet") or "").strip()[:280]
            taken = 0
            for m in r.get("media") or []:
                url = m.get("imageUrl")
                if m.get("type") != "image" or not url or url in seen or taken >= PER_REVIEW:
                    continue
                seen.add(url)
                taken += 1
                out.append(PhotoCandidate(
                    url=_hi(url), page_url=r.get("link") or f"https://www.google.com/maps/place/?q=place_id:{pl['placeId']}",
                    source="map_review", title=None, text=snippet, author=author, date=date,
                    date_source="review" if date else None,
                    license="© автор отзыва в Google Картах; показано превью со ссылкой на источник",
                    collector=f"gmaps:{pl['placeId']}",
                ))
        token = d.get("nextPageToken")
        if not token or len(out) >= WANT_PHOTOS:
            break
    log.info("map_reviews %s: %d photos from %s", uni.qid, len(out), pl["title"])
    return out[:limit]
