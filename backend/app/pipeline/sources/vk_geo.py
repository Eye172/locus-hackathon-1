"""Photos ordinary people geotagged at the campus (VK API, free service token).

Why this source exists: every other "student atmosphere" idea starts from *text* — a hashtag, a keyword, the name of
the university — and text is exactly what stock photos, city landmarks and other campuses also match. Here the anchor
is the coordinate: `photos.search` returns public photos whose author placed them on the map within N metres of the
campus. Nobody geotags a stock photo. What comes back is the ordinary side of student life: a corridor before an exam,
first snow on the square, a queue in the canteen.

The geotag is a claim, not a proof (people move the pin), so the photo still goes through the same funnel: the campus
polygon in verify.py scores the distance, the AI inspector says what is actually in the frame, and close-ups of one
person are dropped — a private selfie near the campus is neither useful nor ours to show.

Cost: nothing. VK's radius parameter accepts only 10 / 100 / 800 / 6000 m; one call returns a few dozen recent photos,
so we sweep a handful of time windows in parallel to reach further back than the last few weeks.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ... import http
from ...config import settings
from ...models import PhotoCandidate, University

log = logging.getLogger("campuslens.vk_geo")

API = "https://api.vk.com/method/photos.search"
RADIUS_M = 800          # VK accepts 10 / 100 / 800 / 6000 only; 800 covers a campus, 6000 covers the whole city
YEAR = 365 * 86400
WINDOWS = [(0, 1), (1, 2), (2, 4), (4, 8)]   # years back: recent photos first, then deeper into the archive
PER_CALL = 50
MIN_SIDE = 500          # VK keeps tiny copies of old uploads; below this there is nothing to look at
WANT_SIDE = 1080        # VK stores up to 2560 px; a phone shot at 2560 is 2 MB we would only downscale anyway
PER_AUTHOR = 3          # one person uploads ten shots of the same lecture hall: an album, not their album


async def _search(lat: float, lon: float, start_y: int, end_y: int) -> list[dict]:
    now = int(time.time())
    try:
        r = await http.get(API, params={
            "lat": lat, "long": lon, "radius": RADIUS_M, "count": PER_CALL, "sort": 0,
            "start_time": now - end_y * YEAR, "end_time": now - start_y * YEAR,
            "access_token": settings.vk_service_token, "v": "5.199"}, timeout=6.0)
        j = r.json()
    except Exception as e:  # noqa: BLE001
        log.info("vk_geo request failed: %r", e)
        return []
    if "error" in j:
        log.info("vk_geo error %s: %s", j["error"].get("error_code"), j["error"].get("error_msg"))
        return []
    return j.get("response", {}).get("items", [])


async def collect(uni: University, limit: int = 40) -> list[PhotoCandidate]:
    if not settings.vk_service_token or uni.lat is None or uni.lon is None:
        return []
    sem = asyncio.Semaphore(3)   # a service token is allowed 3 requests a second

    async def one(w: tuple[int, int]) -> list[dict]:
        async with sem:
            return await _search(uni.lat, uni.lon, *w)

    batches = await asyncio.gather(*[one(w) for w in WINDOWS])
    out: list[PhotoCandidate] = []
    seen: set[str] = set()
    by_author: dict[int, int] = {}
    for items in batches:
        for it in items:
            owner, pid = it.get("owner_id"), it.get("id")
            key = f"{owner}_{pid}"
            if owner is None or pid is None or key in seen or by_author.get(owner, 0) >= PER_AUTHOR:
                continue
            sizes = [s for s in (it.get("sizes") or []) if s.get("url") and s.get("width") and s.get("height")]
            big = max(sizes, key=lambda s: s["width"] * s["height"], default=None)
            if not big or min(big["width"], big["height"]) < MIN_SIDE:
                continue
            # download the smallest copy that is still worth looking at: collection runs against a 25 s budget
            best = min((s for s in sizes if max(s["width"], s["height"]) >= WANT_SIDE),
                       key=lambda s: s["width"] * s["height"], default=big)
            seen.add(key)
            by_author[owner] = by_author.get(owner, 0) + 1
            text = " ".join((it.get("text") or "").split())[:200]
            profile = f"vk.com/{'id' + str(owner) if owner > 0 else 'club' + str(-owner)}"
            out.append(PhotoCandidate(
                url=best["url"], page_url=f"https://vk.com/photo{owner}_{pid}", source="vk_geo",
                title=text[:120] or None, text=text, author=profile,
                license="© автор во ВКонтакте (превью со ссылкой на фото)",
                date=_date(it.get("date")), date_source="post" if it.get("date") else None,
                lat=it.get("lat"), lon=it.get("long"),
                width=best.get("width"), height=best.get("height"), collector=f"vk:geo{RADIUS_M}",
            ))
    log.info("vk_geo %s: %d photos within %d m", uni.qid, len(out), RADIUS_M)
    return out[:limit]


def _date(ts) -> str | None:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None
