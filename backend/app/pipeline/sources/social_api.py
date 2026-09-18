"""Social platforms through APIs instead of page scraping.

- ScrapeCreators (SCRAPECREATORS_API_KEY): posts of the university's own Instagram and TikTok accounts (the links
  come from its website or Wikidata), posts by *other people* that tag the university's Instagram account, and a
  TikTok keyword search for student videos about it. 1 credit per call, responses are cached for 12 h so a rebuilt
  profile costs nothing.
  The university's own feed is a press office: award ceremonies, posters, officials at a table. The posts that tag
  it are the other half of the story - a club rehearsal, a dorm kitchen, a queue at the canteen - shot by students
  who had no reason to stage anything. The tag is an anchor to the university, not a certificate: the photo still
  goes through the AI inspector, and without its verdict nothing from here is shown.
- YouTube Data API (YOUTUBE_API_KEY): videos about the university from any channel; we take YouTube's own stills
  from inside each video (maxres1-3), not the designed cover.

Everything is a candidate for the AI inspector; every photo links to its post. CDN links of Instagram and TikTok
expire within days, the profile shows our 640 px preview and the link to the original post.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from itertools import zip_longest

from ... import cache, http
from ...config import settings
from ...models import PhotoCandidate, University
from .. import video_frames

log = logging.getLogger("campuslens.social_api")
SC = "https://api.scrapecreators.com"
HANDLE = re.compile(r"(?:instagram\.com|tiktok\.com)/@?([A-Za-z0-9_.]+)", re.I)
PER_AUTHOR = 3  # photos kept from one tagging account


def handle_of(url: str | None) -> str | None:
    m = HANDLE.search(url or "")
    h = m.group(1) if m else None
    return h if h and h.lower() not in {"p", "reel", "explore", "accounts", "share"} else None


def _date(ts) -> str | None:
    """Epoch seconds (feed endpoints) or an ISO timestamp (top search)."""
    if isinstance(ts, str) and len(ts) >= 10 and ts[:4].isdigit():
        return ts[:10]
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


_locks: dict[str, asyncio.Lock] = {}


async def _sc(path: str, **params) -> dict | None:
    if not settings.scrapecreators_api_key:
        return None
    key = f"{path}?{sorted(params.items())}"
    # the feed answers both instagram_posts and the id lookup of instagram_tagged, and they run side by side:
    # without this lock the same page would be bought twice
    async with _locks.setdefault(key, asyncio.Lock()):
        hit = await cache.kv_get("social", key, max_age_s=12 * 3600)
        if hit is not None:
            return hit
        return await _sc_fetch(path, key, params)


async def _sc_fetch(path: str, key: str, params: dict) -> dict | None:
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
SLIDES = 6      # a carousel runs to 10 slides; past the sixth it is usually the same room from the same angle


async def instagram_posts(url: str, limit: int = 30) -> list[PhotoCandidate]:
    h = handle_of(url)
    j = await _sc("/v2/instagram/user/posts", handle=h) if h else None
    out: list[PhotoCandidate] = []
    for it in (j or {}).get("items", []):
        cap = it.get("caption")
        text = (cap.get("text") if isinstance(cap, dict) else cap) or ""
        media = it.get("carousel_media") or [it]
        for k, m in enumerate(media[:SLIDES]):
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


async def _user_pk(handle: str) -> str | None:
    """The numeric id behind a handle. It rides along in the feed response we fetch anyway (cached, so free)."""
    j = await _sc("/v2/instagram/user/posts", handle=handle)
    for it in (j or {}).get("items", []):
        pk = (it.get("user") or {}).get("pk")
        if pk:
            return str(pk)
    return None


async def instagram_tagged(url: str, limit: int = 24) -> list[PhotoCandidate]:
    """Posts by other accounts that tagged the university - student clubs, teams, graduates."""
    h = handle_of(url)
    pk = await _user_pk(h) if h else None
    j = await _sc("/v1/instagram/user/tagged-posts", user_id=pk) if pk else None
    out: list[PhotoCandidate] = []
    by_author: dict[str, int] = {}
    for it in (j or {}).get("posts", []):
        it = it.get("media", it)
        author = (it.get("user") or {}).get("username") or ""
        if by_author.get(author, 0) >= PER_AUTHOR:
            continue          # one active club would otherwise fill the whole album with its own season
        cap = it.get("caption")
        text = (cap.get("text") if isinstance(cap, dict) else cap) or ""
        media = it.get("carousel_media") or [it]
        for k, m in enumerate(media[:SLIDES]):
            best = _best((m.get("image_versions2") or {}).get("candidates") or [])
            if not best or not best.get("url"):
                continue
            by_author[author] = by_author.get(author, 0) + 1
            out.append(PhotoCandidate(
                url=best["url"], page_url=f"https://www.instagram.com/p/{it.get('code')}/",
                source="instagram_tagged", title=(" ".join(text.split())[:110] or None), text=f"{text} @{author} @{h}",
                author=f"@{author}" if author else None,
                license="© автор публикации в Instagram (превью со ссылкой на пост)",
                date=_date(it.get("taken_at")), date_source="post" if it.get("taken_at") else None,
                width=best.get("width"), height=best.get("height"), collector=f"scrapecreators:ig_tagged:{k}",
            ))
        if len(out) >= limit:
            break
    return out[:limit]


# ---------- TikTok ----------
def _jpeg(img: dict | None) -> str | None:
    urls = (img or {}).get("url_list") or []
    return next((u for u in urls if ".jpeg" in u or ".jpg" in u), None)


MAX_VIDEOS = 4      # videos we open per source: each costs one small download and two ffmpeg seeks


def _aweme(a: dict) -> dict:
    return a.get("aweme_info", a)


def _page(a: dict) -> tuple[str, str, str]:
    author = (a.get("author") or {}).get("unique_id") or ""
    return author, f"https://www.tiktok.com/@{author}/video/{a.get('aweme_id') or a.get('id')}",         " ".join((a.get("desc") or "").split())


def _photo_posts(awemes: list[dict], source: str, limit: int) -> list[PhotoCandidate]:
    """TikTok's "Photo" tab: slideshow posts. These are already photographs - no cover, no frame extraction - and
    they are where a student posts a set of stills of the campus instead of a clip."""
    out: list[PhotoCandidate] = []
    by_author: dict[str, int] = {}
    for raw in awemes:
        a = _aweme(raw)
        # top search returns a slideshow as plain urls; the feed endpoints wrap them in image_post_info
        urls = [u for u in (a.get("images") or []) if isinstance(u, str)]
        if not urls:
            urls = [u for im in ((a.get("image_post_info") or {}).get("images") or [])
                    if (u := _jpeg(im.get("display_image")))]
        author, page, desc = _page(a)
        if not urls or by_author.get(author, 0) >= PER_AUTHOR * 2:
            continue
        for k, u in enumerate(urls[:SLIDES]):
            by_author[author] = by_author.get(author, 0) + 1
            out.append(PhotoCandidate(
                url=u, page_url=page, source=source,
                title=(f"TikTok @{author}: {desc[:80]}" if desc else f"TikTok @{author}"),
                text=f"{desc} @{author}", author=f"@{author}",
                license="© автор публикации в TikTok (превью со ссылкой на пост)",
                date=_date(a.get("create_time")), date_source="post" if a.get("create_time") else None,
                collector=f"tiktok:photo{k}",
            ))
        if len(out) >= limit:
            break
    return out[:limit]


async def _tiktok_frames(awemes: list[dict], source: str, limit: int,
                         max_videos: int | None = None) -> list[PhotoCandidate]:
    """Frames from inside the videos. The cover is a designed poster; the footage behind it is the campus."""
    picked: list[dict] = []
    seen_authors: set[str] = set()
    for raw in awemes:
        a = _aweme(raw)
        author = (a.get("author") or {}).get("unique_id") or ""
        v = a.get("video") or {}
        url = ((v.get("play_addr") or {}).get("url_list") or [None])[0]
        if not url or (a.get("image_post_info") or {}).get("images"):
            continue          # photo carousels already carry real photos: _tiktok_items handles them
        if author in seen_authors:
            continue          # eight different students beat eight clips by one of them
        seen_authors.add(author)
        picked.append(a)
        if len(picked) >= (max_videos or MAX_VIDEOS):
            break
    if not picked:
        return []
    jobs = [(((a.get("video") or {}).get("play_addr") or {}).get("url_list")[0],
             (a.get("video") or {}).get("duration", 0) / 1000,
             str(a.get("aweme_id") or a.get("id"))) for a in picked]
    out: list[PhotoCandidate] = []
    for a, paths in zip(picked, await video_frames.many(jobs)):
        author, page, desc = _page(a)
        for k, path in enumerate(paths):
            out.append(PhotoCandidate(
                url=f"file:{path}", page_url=page, source=source,
                title=(f"Кадр из видео @{author}: {desc[:80]}" if desc else f"Кадр из видео @{author}"),
                text=f"{desc} @{author}", author=f"@{author}",
                license="© автор видео в TikTok (кадр со ссылкой на видео)",
                date=_date(a.get("create_time")), date_source="post" if a.get("create_time") else None,
                collector=f"tiktok:frame{k}",
            ))
    return out[:limit]


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


async def _videos_and_frames(awemes: list[dict], source: str, limit: int,
                             max_videos: int | None = None) -> list[PhotoCandidate]:
    """Slideshow photos first, then frames from inside the clips, then covers as the fallback."""
    photos = _photo_posts(awemes, source, limit)
    items = _tiktok_items(awemes, source, limit)
    if not video_frames.available():
        return (photos + items)[:limit]
    frames = await _tiktok_frames(awemes, source, limit, max_videos)
    # alternate: one long slideshow would otherwise fill the whole quota and no clip would be opened at all
    mixed: list[PhotoCandidate] = []
    for a, b in zip_longest(photos, frames):
        mixed += [c for c in (a, b) if c is not None]
    return (mixed + items)[:limit]


async def tiktok_videos(url: str, limit: int = 16, max_videos: int | None = None) -> list[PhotoCandidate]:
    h = handle_of(url)
    j = await _sc("/v3/tiktok/profile/videos", handle=h) if h else None
    return await _videos_and_frames((j or {}).get("aweme_list") or [], "tiktok", limit, max_videos)


async def tiktok_search(uni: University, limit: int = 12, max_videos: int | None = None) -> list[PhotoCandidate]:
    q = uni.names.get("en") or uni.name
    if len(q) < 6:
        return []
    j = await _sc("/v1/tiktok/search/keyword", query=q)
    return await _videos_and_frames((j or {}).get("search_item_list") or [], "tiktok_search", limit, max_videos)


def short_names(uni: University) -> list[str]:
    """Abbreviations from the aliases, the one that spells the current name first.

    Wikidata keeps old names next to new ones: КазГУ (Государственный, until 1991) sits beside КазНУ (Национальный).
    The capitals of an abbreviation are the initials of the words it stands for, so the alias whose capitals are
    the initials of today's name is today's abbreviation - for any university, without a list of them."""
    initials = {w[0].lower() for n in [*uni.names.values(), uni.name] if n for w in re.split(r"[\s\-]+", n) if w}
    cands = {a.strip() for a in uni.aliases
             if 4 <= len(_tag(a)) <= 8 and " " not in a.strip() and not re.search(r"[./@]", a)}  # not "ucm.es"

    def fit(a: str) -> float:
        caps = [c.lower() for c in a if c.isupper()]
        return sum(c in initials for c in caps) / len(caps) if caps else 0.0

    return sorted(cands, key=lambda a: (-fit(a), not a.isascii(), len(a), a))


def search_queries(uni: University) -> list[str]:
    """What a person types into a social network's search box to find this university.

    The official Russian name ("Казахстанско-Британский технический университет") returns nothing at all on TikTok:
    nobody types it. What people type is the English name and the short name everyone uses - KBTU, KazNU, AITU.
    Built from the university's own names and aliases, so it works the same for any university in the index or on
    the map, not for a hand-picked list."""
    from ..langs import local_lang
    out: list[str] = []
    en = uni.names.get("en")
    native = uni.names.get(local_lang(uni)) or uni.name   # the name in the university's own language
    if en and len(en) >= 6:
        out.append(en)
    shorts = short_names(uni)
    if shorts:
        out.append(shorts[0])
    if native and len(native) <= 40 and native not in out:
        out.append(native)
    return list(dict.fromkeys(out))[:3] or [uni.name]


def _latin_ext(text: str) -> bool:
    """Latin script, accents included (Atmosphäre, życie, öğrenci)."""
    return all(ord(c) < 0x250 or not c.isalpha() for c in text)


def social_queries(uni: University, deep: bool = False) -> list[str]:
    """Every query CampusLens itself sends to TikTok and Instagram for this university - any university in the world.

    The bare name finds the university's own posts and videos about admission. The photos that show what it is like
    to be there come from queries about the place - "<name> campus", "<name> student life", "<name> dorm",
    "<name> atmosphere" - in English for everyone, and again in the language its students write in: "TUM
    Studentenleben", "東京大学 大学生活", "KBTU общежитие". Whatever comes back is only a candidate: the inspector
    decides, as for every other source.
    The fast pass sends the names and the two most productive place queries per language; the deep pass all of them."""
    from ..langs import CONCEPTS, FAST_EN, FAST_LOCAL, local_lang
    base = search_queries(uni)
    en = uni.names.get("en") or base[0]
    en_words = list(CONCEPTS["en"].values()) if deep else [CONCEPTS["en"][c] for c in FAST_EN]
    out = base + [f"{en} {w}" for w in en_words]
    lang = local_lang(uni)
    if lang != "en" and lang in CONCEPTS:
        # with the abbreviation if people use one in that script (КБТУ общежитие, TUM Studentenleben), else with the
        # name in that language (東京大学 キャンパス): a latin abbreviation next to Japanese words is nobody's query
        words = list(CONCEPTS[lang].values()) if deep else [CONCEPTS[lang][c] for c in FAST_LOCAL]
        latin = all(_latin_ext(w) for w in words)
        head = next((a for a in short_names(uni) if _latin_ext(a) == latin), None) or uni.names.get(lang) or uni.name
        out += [f"{head} {w}" for w in words]
    return list(dict.fromkeys(q for q in out if q))


def _round_robin(lists: list[list[dict]], key) -> list[dict]:
    """One from each query in turn, so a per-source limit samples every query instead of exhausting the first."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in zip_longest(*lists):
        for it in row:
            if it is None:
                continue
            k = key(it)
            if k and k not in seen:
                seen.add(k)
                out.append(it)
    return out


async def _paged(path: str, pages: int, key: str = "cursor", items: str = "items", **params) -> list[dict]:
    """Up to `pages` pages of one search; each page is one credit and cached like any other call."""
    out: list[dict] = []
    cursor = None
    for _ in range(max(1, pages)):
        j = await _sc(path, **params, **({key: cursor} if cursor else {}))
        out += (j or {}).get(items) or []
        cursor = (j or {}).get("cursor")
        if not cursor or (j or {}).get("has_more") is False:
            break
    return out


async def tiktok_top(uni: University, limit: int = 14, max_videos: int | None = None,
                     pages: int = 1, deep: bool = False) -> list[PhotoCandidate]:
    """TikTok's "Top" search, run by CampusLens for the name and for the place queries (campus, student life,
    atmosphere, dorm). It is the only endpoint that also returns the slideshow posts of the Photo tab."""
    qs = social_queries(uni, deep)
    found = await video_frames.until_deadline([_paged("/v1/tiktok/search/top", pages if i == 0 else 1, query=q)
                                               for i, q in enumerate(qs)], margin=6.0)
    uniq = _round_robin([f or [] for f in found], lambda it: str(it.get("id") or it.get("aweme_id") or ""))
    return await _videos_and_frames(uniq, "tiktok_top", limit, max_videos)


# ---------- Instagram search ----------
async def instagram_search(uni: University, limit: int = 16, max_videos: int = 4,
                           pages: int = 1, deep: bool = False) -> list[PhotoCandidate]:
    """What Instagram shows when you type the university into its search: the Popular page for the name and the
    posts under its hashtag. Students' reels, club posts, move-in days - and, because a name is only a name, also
    posts about Nazarbayev Intellectual Schools when you asked for Nazarbayev University. That is why these are
    search hits (verify.SEARCH_SOURCES): the inspector has to recognise the campus, a matching caption is not enough.

    Almost every post here is a reel whose cover is a title card, so the reel is opened and two frames are taken from
    inside it, as with TikTok; a plain photo post is used as it is."""
    qs = social_queries(uni, deep)
    tags = hashtags(uni)
    found = await video_frames.until_deadline(
        [*[_paged("/v1/instagram/search/popular", pages if i == 0 else 1, items="posts", query=q) for i, q in enumerate(qs)],
         *[_paged("/v1/instagram/search/hashtag", 1, items="posts", hashtag=t, media_type="all") for t in tags]],
        margin=6.0)
    posts = _round_robin([f or [] for f in found], lambda p: p.get("shortcode") or p.get("id"))
    by_author: dict[str, int] = {}
    picked: list[dict] = []
    for p in posts:
        owner = ((p.get("owner") or {}).get("username") or "").lower()
        if by_author.get(owner, 0) >= PER_AUTHOR:
            continue
        by_author[owner] = by_author.get(owner, 0) + 1
        picked.append(p)
    videos = [p for p in picked if p.get("video_url")][:max_videos] if video_frames.available() else []
    frames = await video_frames.many([(p["video_url"], float(p.get("video_duration") or 12.0), f"ig_{p.get('shortcode')}")
                                      for p in videos])
    frame_of = {id(p): paths for p, paths in zip(videos, frames)}
    out: list[PhotoCandidate] = []
    for p in picked:
        owner = (p.get("owner") or {}).get("username") or ""
        cap = p.get("caption")
        text = " ".join(((cap.get("text") if isinstance(cap, dict) else cap) or "").split())
        page = p.get("url") or f"https://www.instagram.com/p/{p.get('shortcode')}/"
        base = dict(page_url=page, source="instagram_search", text=f"{text} @{owner}", author=f"@{owner}",
                    date=_date(p.get("taken_at")), date_source="post" if p.get("taken_at") else None)
        paths = frame_of.get(id(p))
        if paths:
            for k, path in enumerate(paths):
                out.append(PhotoCandidate(url=f"file:{path}", title=f"Кадр из рилса @{owner}: {text[:80]}".strip(": "),
                                          license="© автор публикации в Instagram (кадр со ссылкой на пост)",
                                          collector=f"instagram:frame{k}", **base))
        elif p.get("display_url"):
            # a photo post is the picture itself; for a reel we did not open it is the cover - often a title card,
            # which the inspector rejects, sometimes a plain shot of the campus, which it keeps
            out.append(PhotoCandidate(url=p["display_url"], title=f"Instagram @{owner}: {text[:80]}".strip(": "),
                                      license="© автор публикации в Instagram (превью со ссылкой на пост)",
                                      collector="instagram:cover" if p.get("video_url") else "instagram:photo", **base))
        if len(out) >= limit:
            break
    return out[:limit]


def _tag(name: str | None) -> str:
    return re.sub(r"[^\w]+", "", (name or "").lower(), flags=re.U)


def hashtags(uni: University) -> list[str]:
    """How students write the university as one word.

    Two shapes cover almost everything: the full name run together (#nazarbayevuniversity) and the abbreviation
    everyone actually types (#kbtu). The long form is precise; the short one is where the student videos are, and
    it is also where another university with the same initials lives - which is why hashtag hits are treated as
    search results: no name in the caption, and the inspector has to be certain (verify.SEARCH_SOURCES)."""
    out: list[str] = []
    for name in [uni.names.get("en"), uni.names.get("ru") or uni.name]:
        t = _tag(name)
        if 12 <= len(t) <= 40 and t not in out:
            out.append(t)
    # latin first (TikTok tags are mostly transliterated), then shortest, then alphabetically so the choice
    # is the same on every run
    short = [_tag(a) for a in short_names(uni)]
    return (out[:1] + short[:1]) or out[:2]


async def tiktok_hashtag(uni: University, limit: int = 16, max_videos: int | None = None) -> list[PhotoCandidate]:
    """Videos under the university's own hashtag - posted by students, not by the press office."""
    tags = hashtags(uni)
    if not tags:
        return []
    found = await asyncio.gather(*[_sc("/v1/tiktok/search/hashtag", hashtag=t) for t in tags])
    lists = await asyncio.gather(*[_videos_and_frames((j or {}).get("aweme_list") or [], "tiktok_hashtag", limit, max_videos)
                                   for j in found if (j or {}).get("aweme_list")])
    # the long form is precise, the abbreviation is where the students are: take from both, not all of the first
    out: list[PhotoCandidate] = []
    for row in zip(*lists) if len(lists) > 1 else [(c,) for c in (lists[0] if lists else [])]:
        out += [c for c in row if c]
    return out[:limit]


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
