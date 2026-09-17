"""Social platforms through APIs instead of page scraping.

- ScrapeCreators (SCRAPECREATORS_API_KEY): posts of the university's own Instagram and TikTok accounts (the links
  come from its website) and a TikTok keyword search for student videos about it. 1 credit per call, responses
  are cached for 12 h so a rebuilt profile costs nothing.
- YouTube Data API (YOUTUBE_API_KEY): videos about the university from any channel; we take YouTube's own stills
  from inside each video (maxres1-3), not the designed cover.

Everything is a candidate for the AI inspector; every photo links to its post. CDN links of Instagram and TikTok
expire within days, the profile shows our 640 px preview and the link to the original post.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ... import cache, http
from ...config import settings
from ...models import PhotoCandidate, University

log = logging.getLogger("campuslens.social_api")
SC = "https://api.scrapecreators.com"
HANDLE = re.compile(r"(?:instagram\.com|tiktok\.com)/@?([A-Za-z0-9_.]+)", re.I)


def handle_of(url: str | None) -> str | None:
    m = HANDLE.search(url or "")
    h = m.group(1) if m else None
    return h if h and h.lower() not in {"p", "reel", "explore", "accounts", "share"} else None


def _date(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


async def _sc(path: str, **params) -> dict | None:
    if not settings.scrapecreators_api_key:
        return None
    key = f"{path}?{sorted(params.items())}"
    hit = await cache.kv_get("social", key, max_age_s=12 * 3600)
    if hit is not None:
        return hit
    r = await http.get(SC + path, params=params, headers={"x-api-key": settings.scrapecreators_api_key}, timeout=12.0)
    if r.status_code != 200:
        log.warning("scrapecreators %s -> %s %s", path, r.status_code, r.text[:160])
        return None
    j = r.json()
    log.info("scrapecreators %s: %s credits left", path, j.get("credits_remaining"))
    await cache.kv_set("social", key, j)
    return j


def _best(candidates: list[dict]) -> dict | None:
    return max(candidates, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0), default=None)


# ---------- Instagram ----------
async def instagram_posts(url: str, limit: int = 24) -> list[PhotoCandidate]:
    h = handle_of(url)
    j = await _sc("/v2/instagram/user/posts", handle=h) if h else None
    out: list[PhotoCandidate] = []
    for it in (j or {}).get("items", []):
        cap = it.get("caption")
        text = (cap.get("text") if isinstance(cap, dict) else cap) or ""
        media = it.get("carousel_media") or [it]
        for k, m in enumerate(media[:3]):
            best = _best((m.get("image_versions2") or {}).get("candidates") or [])
            if not best or not best.get("url"):
                continue
            out.append(PhotoCandidate(
                url=best["url"], page_url=f"https://www.instagram.com/p/{it.get('code')}/",
                source="instagram", title=(" ".join(text.split())[:110] or None), text=f"{text} @{h}",
                author=f"@{h}", license="© Instagram-аккаунт вуза (превью со ссылкой на пост)",
                date=_date(it.get("taken_at")), date_source="post" if it.get("taken_at") else None,
                width=best.get("width"), height=best.get("height"), collector=f"scrapecreators:ig:{k}",
            ))
        if len(out) >= limit:
            break
    return out[:limit]


# ---------- TikTok ----------
def _jpeg(img: dict | None) -> str | None:
    urls = (img or {}).get("url_list") or []
    return next((u for u in urls if ".jpeg" in u or ".jpg" in u), None)


def _tiktok_items(awemes: list[dict], source: str, limit: int) -> list[PhotoCandidate]:
    out: list[PhotoCandidate] = []
    for a in awemes:
        a = a.get("aweme_info", a)
        author = (a.get("author") or {}).get("unique_id") or ""
        page = f"https://www.tiktok.com/@{author}/video/{a.get('aweme_id')}"
        desc = " ".join((a.get("desc") or "").split())
        images = ((a.get("image_post_info") or {}).get("images") or [])[:3]
        urls = [(_jpeg(im.get("display_image")), im.get("display_image", {}).get("width"), im.get("display_image", {}).get("height"))
                for im in images]
        if not urls:
            v = a.get("video") or {}
            u = _jpeg(v.get("origin_cover")) or _jpeg(v.get("cover"))
            urls = [(u, None, None)]
        for u, w, hgt in urls:
            if not u:
                continue
            out.append(PhotoCandidate(
                url=u, page_url=page, source=source, title=(f"TikTok @{author}: {desc[:90]}" if desc else f"TikTok @{author}"),
                text=f"{desc} @{author}", author=f"@{author}",
                license="© автор в TikTok (превью со ссылкой на видео)",
                date=_date(a.get("create_time")), date_source="post" if a.get("create_time") else None,
                width=w, height=hgt, collector=f"scrapecreators:{source}",
            ))
        if len(out) >= limit:
            break
    return out[:limit]


async def tiktok_videos(url: str, limit: int = 16) -> list[PhotoCandidate]:
    h = handle_of(url)
    j = await _sc("/v3/tiktok/profile/videos", handle=h) if h else None
    return _tiktok_items((j or {}).get("aweme_list") or [], "tiktok", limit)


async def tiktok_search(uni: University, limit: int = 12) -> list[PhotoCandidate]:
    q = uni.names.get("en") or uni.name
    if len(q) < 6:
        return []
    j = await _sc("/v1/tiktok/search/keyword", query=q)
    return _tiktok_items((j or {}).get("search_item_list") or [], "tiktok_search", limit)


# ---------- YouTube Data API ----------
async def youtube_search(uni: University, videos: int = 6) -> list[PhotoCandidate]:
    if not settings.youtube_api_key:
        return []
    from .web_images import CIS, country_code
    cis = country_code(uni) in CIS
    name = (uni.names.get("ru") or uni.name) if cis else (uni.names.get("en") or uni.name)
    q = f"{name} {'кампус общежитие' if cis else 'campus tour'}"
    hit = await cache.kv_get("social", f"yt:{q}", max_age_s=7 * 86400)
    if hit is None:
        r = await http.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": q, "type": "video", "maxResults": videos, "relevanceLanguage": "ru" if cis else "en",
            "key": settings.youtube_api_key}, timeout=8.0)
        if r.status_code != 200:
            log.warning("youtube search -> %s %s", r.status_code, r.text[:160])
            return []
        hit = r.json().get("items", [])
        await cache.kv_set("social", f"yt:{q}", hit)
    out: list[PhotoCandidate] = []
    for it in hit:
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if not vid:
            continue
        title = sn.get("title") or ""
        for k in (2, 1):
            out.append(PhotoCandidate(
                url=f"https://i.ytimg.com/vi/{vid}/maxres{k}.jpg", page_url=f"https://www.youtube.com/watch?v={vid}",
                source="youtube_search", title=f"Кадр из видео «{title[:100]}»", text=f"{title} {sn.get('channelTitle', '')}",
                author=sn.get("channelTitle"), license="© автор видео на YouTube (кадр со ссылкой на видео)",
                date=(sn.get("publishedAt") or "")[:10] or None, date_source="video" if sn.get("publishedAt") else None,
                collector=f"youtube_api:frame{k}",
            ))
    return out
