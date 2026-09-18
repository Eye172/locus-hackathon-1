"""TikTok and Instagram searched the way students post - theme by theme - and ranked before anything is downloaded.

For every theme of the search plan (atmosphere, a day in the life, campus, dorms, events, library…, see
pipeline/search_plan.py) the networks are asked in English and in the language the university's students write in.
Everything that comes back is scored by how clearly the post itself is about THIS university:
- the caption names it: the full name, the name run together as a hashtag, a brand name, its abbreviation;
- the author is one of its own accounts (clubs, library, dorms, teams - found by the networks' own account search)
  or has the university in their name;
- the post is geotagged at the campus (Instagram), the author's region is the university's country (TikTok);
- the caption is about the theme (dorm, общага, library, fest…);
- ads, paid partnerships, admission agencies (MBBS abroad, grants, visas) and announcements are pushed down;
- photo posts rank a little above clips (they are photographs already), engagement a little.
Only the best few posts per theme are opened: the slides of a photo post, two frames from inside a clip. The audit of
18 Sep found the old search opening 7-16 of ~400 posts it had paid for, picked in the order they came.

Discovery, cached for a week: Instagram's search for the name returns the university's own accounts and the hashtags
people really use (with post counts); TikTok's returns accounts and its autocomplete ("<name> aesthetic",
"<name> dorm") - the phrases people actually type, added as queries of the matching theme.
Everything found is a candidate for the AI inspector, as for every other source.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from ... import cache
from ...geo import haversine_km
from ...models import PhotoCandidate, University
from .. import search_plan as sp
from .. import video_frames
from .social_api import _date, _jpeg, _sc, handle_of

log = logging.getLogger("campuslens.social_search")

SEARCH_TTL = 3 * 86400        # a search answer is reused for three days: the posts do not change that fast
SLIDES = 4                    # slides taken from one photo post
PER_AUTHOR = 2                # posts of one author per theme
COMMERCE = re.compile(
    r"mbbs|study\s+(?:in|abroad)|admission(?:s)?\s+open|consultan|\bvisa\b|apply\s+now|agency|whatsapp|\+91|"
    r"scholarship\s+available|скидк|реклам|промокод|promo\s*code|услуг|под\s+ключ|#ad\b|sponsored|giveaway|розыгрыш",
    re.I)
ANNOUNCE = re.compile(r"register|registration|rsvp|deadline|webinar|save the date|регистрац|дедлайн|вебинар|анонс|"
                      r"приглашаем|announcement", re.I)
NOT_AFFILIATED = re.compile(r"salon|beauty|косметолог|врач|doctor|clinic|shop|store|магазин|аренд|rent|taxi|такси|"
                            r"недвиж|realty|consult|visa|mbbs|гранты|grant|поступлен|admission|abroad|tutor|репетитор|"
                            r"курсы|academy|академия|lash|nail|brow", re.I)
# accounts that sell admission to foreign students (MBBS abroad and the like): they do film the campus, but as an
# advert, and for small universities they outnumber the students who post about it
AGENCY = re.compile(r"consult|edu_?world|edutech|edu_?tech|abroad|mbbs|overseas|admission|visa|study_?in|studyin|"
                    r"education_?(?:center|centre|group|hub)|global_?edu|agency|агентств|поступлени", re.I)
CLUB = re.compile(r"club|society|union|council|team|клуб|совет|команд|studio|magazine|media|radio|tv\b|enactus|tedx|"
                  r"league|лига|fest|dance|music|debate", re.I)


@dataclass
class Post:
    platform: str
    source: str
    id: str
    page: str
    caption: str = ""
    author: str = ""
    author_name: str = ""
    region: str | None = None
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    date: str | None = None
    plays: int = 0
    ad: bool = False
    images: list[str] = field(default_factory=list)
    video: str | None = None
    duration: float = 0.0
    cover: str | None = None
    intent: str = ""
    query: str = ""
    affiliated: bool = False
    anchor: bool = False
    score: float = 0.0
    why: list[str] = field(default_factory=list)


# ---------- parsing ----------
def _tt_post(raw: dict, intent: str, query: str) -> Post | None:
    a = raw.get("aweme_info", raw)
    pid = str(a.get("aweme_id") or a.get("id") or "")
    au = a.get("author") or {}
    uid = au.get("unique_id") or ""
    if not pid or not uid:
        return None
    v = a.get("video") or {}
    images = [u for u in (a.get("images") or []) if isinstance(u, str)]
    if not images:
        images = [u for im in ((a.get("image_post_info") or {}).get("images") or [])
                  if (u := _jpeg(im.get("display_image")))]
    play = ((v.get("play_addr") or {}).get("url_list") or [None])[0]
    dur = v.get("duration") or 0
    return Post(platform="tiktok", source="tiktok_search", id=pid, page=f"https://www.tiktok.com/@{uid}/video/{pid}",
                caption=" ".join((a.get("desc") or "").split()), author=uid, author_name=au.get("nickname") or "",
                region=a.get("region"), date=_date(a.get("create_time")),
                plays=int((a.get("statistics") or {}).get("play_count") or 0),
                ad=bool(a.get("is_ad") or a.get("is_ads") or a.get("is_paid_partnership")),
                images=images, video=None if images else play, duration=dur / 1000 if dur > 1000 else float(dur),
                cover=_jpeg(v.get("origin_cover")) or _jpeg(v.get("cover")), intent=intent, query=query)


def _ig_post(p: dict, intent: str, query: str, source: str = "instagram_search") -> Post | None:
    """A post from the hashtag / popular search (flat shape) or from an account's feed (v2 shape)."""
    code = p.get("shortcode") or p.get("code")
    owner = p.get("owner") or p.get("user") or {}
    if not code:
        return None
    cap = p.get("caption")
    text = (cap.get("text") if isinstance(cap, dict) else cap) or ""
    loc = p.get("location") or {}
    images: list[str] = []
    video = None
    if "image_versions2" in p or "carousel_media" in p:          # v2 feed item
        for m in (p.get("carousel_media") or [p])[:SLIDES]:
            cands = (m.get("image_versions2") or {}).get("candidates") or []
            best = max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0), default=None)
            if best and best.get("url") and not m.get("video_versions"):
                images.append(best["url"])
        vv = p.get("video_versions") or []
        if vv and not images:
            video = vv[0].get("url")
        cover = (max(((p.get("image_versions2") or {}).get("candidates") or [{}]),
                     key=lambda c: (c.get("width") or 0) * (c.get("height") or 0)) or {}).get("url")
    else:
        if p.get("video_url"):
            video = p["video_url"]
        elif p.get("display_url"):
            images = [p["display_url"]]
        cover = p.get("display_url") or p.get("thumbnail_src")
    return Post(platform="instagram", source=source, id=code,
                page=p.get("url") or f"https://www.instagram.com/p/{code}/", caption=" ".join(text.split()),
                author=owner.get("username") or "", author_name=owner.get("full_name") or "",
                lat=loc.get("lat"), lon=loc.get("lng"), place=loc.get("name"), date=_date(p.get("taken_at")),
                plays=int(p.get("play_count") or p.get("video_view_count") or p.get("like_count") or 0),
                ad=bool(p.get("is_ad") or p.get("is_paid_partnership")), images=images, video=video,
                duration=float(p.get("video_duration") or 0) or 12.0, cover=cover, intent=intent, query=query)


# ---------- relevance ----------
def _flat(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower(), flags=re.U)


def _has_abbr(text: str, abbr: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(abbr)}(?![\w])", text, re.U) is not None


def score(p: Post, forms: dict[str, list[str]], iso: str | None, center: tuple[float, float] | None,
          intent: sp.Intent | None) -> float:
    cap = p.caption.lower()
    flat = _flat(p.caption)
    s, why = 0.0, []
    if any(f in cap for f in forms["full"]) or any(t in flat for t in forms["tags"]):
        s += 3.0
        why.append("name")
    else:
        hit = next((a for a in forms["abbr"] if _has_abbr(cap, a)), None)
        if hit:
            s += 1.5 if len(hit) >= 4 else 1.0
            why.append("abbr")
    who = re.sub(r"[_.]", " ", f"{p.author} {p.author_name}".lower())
    if p.affiliated:
        s += 1.5
        why.append("account")
    elif any(t in _flat(who) for t in forms["tags"]) or any(len(a) >= 4 and _has_abbr(who, a) for a in forms["abbr"]):
        s += 1.0
        why.append("author")
    if intent and any(w in cap for w in intent.caption_words):
        s += 1.2
        why.append("theme")
    if p.region and iso:
        if p.region.upper() == iso:
            s += 0.8
            why.append("region")
        elif "name" not in why:
            s -= 0.8
    if p.lat is not None and p.lon is not None and center:
        d = haversine_km(center[0], center[1], p.lat, p.lon)
        s += 3.0 if d <= 2 else 1.0 if d <= 30 else -3.0 if d > 150 else 0.0
        why.append(f"geo{d:.0f}km")
    if p.ad:
        s -= 3.0
    if not p.affiliated and AGENCY.search(f"{p.author} {p.author_name}"):
        s -= 2.5
        why.append("agency")
    if COMMERCE.search(p.caption):
        s -= 1.5
        why.append("commerce")
    if ANNOUNCE.search(p.caption):
        s -= 0.5
    if p.images:
        s += 0.5
    s += min(0.4, 0.07 * math.log10(1 + p.plays))
    if p.date and p.date[:4].isdigit() and int(p.date[:4]) < datetime.now().year - 5:
        s -= 0.5
    p.score, p.why = round(s, 2), why
    # something that ties the post to the university itself, not just to the theme and the country: without it a
    # "dorm" query in Kokshetau returns every dorm in town
    p.anchor = any(w in ("name", "abbr", "account", "author") or (w.startswith("geo") and int(w[3:-2]) <= 30)
                   for w in why)
    return p.score


# ---------- discovery ----------
_discover_locks: dict[str, asyncio.Lock] = {}


async def discover(uni: University, forms: dict[str, list[str]], plan: sp.Plan) -> dict:
    """The university's own accounts and hashtags on Instagram, its accounts and autocomplete on TikTok.
    TikTok, Instagram and the accounts source ask for it at the same moment: one of them looks, the others wait."""
    async with _discover_locks.setdefault(uni.qid, asyncio.Lock()):
        hit = await cache.kv_get("discover", uni.qid, max_age_s=7 * 86400)
        if hit is not None:
            return hit
        return await _discover(uni, forms, plan)


async def known(uni: University, forms: dict[str, list[str]], plan: sp.Plan, fast: bool) -> dict:
    """For the first profile: the discovery already cached, or nothing (it is started for the background pass).
    Waiting for it cost the fast searches 3-5 of their ~12 seconds, and on Toronto they came back empty."""
    if not fast:
        return await discover(uni, forms, plan)
    hit = await cache.kv_get("discover", uni.qid, max_age_s=7 * 86400)
    if hit is None:
        asyncio.ensure_future(discover(uni, forms, plan))
    return hit or {}


async def _discover(uni: University, forms: dict[str, list[str]], plan: sp.Plan) -> dict:
    en = uni.names.get("en") or uni.name
    # a latin brand besides the official name ("Ualikhanov University" for the Kokshetau State University)
    names = list(dict.fromkeys([en, *[b for b in sp.brand_names(uni) if b.isascii() and " " in b][:1]]))
    ig_calls = [_sc("/v1/instagram/search", _max_age=7 * 86400, query=n) for n in names]
    tt_users = _sc("/v1/tiktok/search/users", _max_age=7 * 86400, query=en)
    tt_sugg = _sc("/v1/tiktok/search/suggestions", _max_age=7 * 86400, query=en) if plan.use_suggestions else None
    res = await asyncio.gather(*ig_calls, tt_users, *([tt_sugg] if tt_sugg else []), return_exceptions=True)
    res = [r if isinstance(r, dict) else {} for r in res]
    igs, tu, ts = res[:len(names)], res[len(names)], (res[len(names) + 1] if tt_sugg else {})
    official = (handle_of(uni.social.get("instagram")) or "").lower()

    def affiliated(text: str) -> bool:
        t = text.lower()
        return (any(f in t for f in forms["full"]) or any(x in _flat(t) for x in forms["tags"])
                or any(len(a) >= 4 and _has_abbr(t.replace("_", " ").replace(".", " "), a) for a in forms["abbr"]))

    accounts: list[dict] = []
    seen: set[str] = set()
    for ig in igs:
        for u in ig.get("users") or []:
            name, full = u.get("username") or "", u.get("full_name") or ""
            text = f"{name} {full}"
            if not name or name.lower() in seen or name.lower() == official or not affiliated(text) or \
                    NOT_AFFILIATED.search(text) or AGENCY.search(text) or re.search(r"indian|pakistan|nepal|bangla", text, re.I):
                continue
            seen.add(name.lower())
            intent = next((i.key for i in plan.intents if any(w in text.lower() for w in i.account_words)), None)
            if intent is None and CLUB.search(text):
                intent = "events"
            accounts.append({"username": name, "full_name": full, "intent": intent or "atmosphere"})
    tags: list[dict] = []
    for ig in igs:
        for h in ig.get("hashtags") or []:
            tag, n = (h.get("name") or "").lower(), int(h.get("media_count") or 0)
            if n < 10 or tag in {t["tag"] for t in tags} or not (any(x in tag for x in forms["tags"]) or
                                                                 any(len(a) >= 4 and tag.startswith(_flat(a))
                                                                     for a in forms["abbr"])):
                continue
            intent = next((i.key for i in plan.intents
                           if any(_flat(w) and _flat(w) in tag and w.isascii() for w in i.caption_words)), None)
            tags.append({"tag": tag, "count": n, "intent": intent or "atmosphere"})
    tags.sort(key=lambda t: -t["count"])
    tt_accounts = [{"username": (u.get("user_info") or u).get("unique_id"),
                    "name": (u.get("user_info") or u).get("nickname")}
                   for u in tu.get("user_list") or []
                   if affiliated(f"{(u.get('user_info') or u).get('unique_id')} {(u.get('user_info') or u).get('nickname')}")]
    suggestions: list[dict] = []
    for s in ts.get("suggestions") or []:
        text = (s.get("text") or "").lower().strip()
        if not text or text in forms["full"] or not affiliated(text):
            continue
        rest = text
        for f in forms["full"] + forms["abbr"]:
            rest = rest.replace(f, " ")
        intent = next((i.key for i in plan.intents if any(w in rest for w in i.caption_words)), None)
        if intent is None and re.search(r"edit|aesthetic|vibe", rest):
            intent = "atmosphere"
        if intent:
            suggestions.append({"query": text, "intent": intent})
    out = {"ig_accounts": accounts[:12], "ig_tags": tags[:6], "tt_accounts": tt_accounts[:8],
           "suggestions": suggestions[:8]}
    await cache.kv_set("discover", uni.qid, out)
    log.info("discover %s: %d IG accounts, tags %s, suggestions %s", uni.qid, len(accounts),
             [t["tag"] for t in tags[:6]], [s["query"] for s in suggestions])
    return out


# ---------- selection and media ----------
def _select(posts: list[Post], plan: sp.Plan, per_intent: int) -> list[Post]:
    """The best posts per theme: above the relevance floor, at most PER_AUTHOR per author, each post once (under
    the theme where it scored best)."""
    best: dict[str, Post] = {}
    for p in posts:
        if p.id not in best or p.score > best[p.id].score:
            best[p.id] = p
    by_intent: dict[str, list[Post]] = {}
    for p in sorted(best.values(), key=lambda p: -p.score):
        if p.score < plan.min_relevance or not p.anchor:
            continue
        lst = by_intent.setdefault(p.intent, [])
        if len(lst) >= per_intent or sum(x.author == p.author for x in lst) >= PER_AUTHOR:
            continue
        lst.append(p)
    return [p for lst in by_intent.values() for p in lst]


async def _media(picked: list[Post], max_videos: int, uni: University) -> list[PhotoCandidate]:
    videos: list[Post] = []
    per_intent: dict[str, int] = {}
    for p in picked:
        if p.video and not p.images and per_intent.get(p.intent, 0) < max_videos and video_frames.available():
            per_intent[p.intent] = per_intent.get(p.intent, 0) + 1
            videos.append(p)
    frames = await video_frames.many([(p.video, p.duration or 12.0, f"{p.platform[:2]}_{p.id}") for p in videos])
    frame_of = {p.id: paths for p, paths in zip(videos, frames)}
    center = (uni.lat, uni.lon) if uni.lat is not None else None
    out: list[PhotoCandidate] = []
    for p in picked:
        net = "TikTok" if p.platform == "tiktok" else "Instagram"
        base = dict(page_url=p.page, source=p.source, text=f"{p.caption} @{p.author}", author=f"@{p.author}",
                    date=p.date, date_source="post" if p.date else None, intent=p.intent, query=p.query,
                    relevance=p.score)
        if p.lat is not None and center and haversine_km(center[0], center[1], p.lat, p.lon) <= 1.5:
            base |= {"lat": p.lat, "lon": p.lon}    # a place-level geotag; a city-level one is not a photo location
        cap = p.caption[:80]
        if p.images:
            for k, u in enumerate(p.images[:SLIDES]):
                out.append(PhotoCandidate(url=u, title=f"{net} @{p.author}: {cap}".strip(": "),
                                          license=f"© автор публикации в {net} (превью со ссылкой на пост)",
                                          collector=f"{p.platform}:photo{k}", **base))
        elif frame_of.get(p.id):
            for k, path in enumerate(frame_of[p.id]):
                out.append(PhotoCandidate(url=f"file:{path}", title=f"Кадр из видео @{p.author}: {cap}".strip(": "),
                                          license=f"© автор видео в {net} (кадр со ссылкой на пост)",
                                          collector=f"{p.platform}:frame{k}", **base))
        elif p.cover:
            # a clip we did not open: its cover, often a title card the inspector rejects, sometimes the place
            out.append(PhotoCandidate(url=p.cover, title=f"{net} @{p.author}: {cap}".strip(": "),
                                      license=f"© автор публикации в {net} (превью со ссылкой на пост)",
                                      collector=f"{p.platform}:cover", **base))
    return out


def _context(uni: University, up: sp.UniPlan) -> tuple[dict, str | None, tuple[float, float] | None]:
    from ..langs import iso_of
    forms = sp.name_forms(uni, up.names)
    return forms, iso_of(uni), ((uni.lat, uni.lon) if uni.lat is not None else None)


def _first_per_lang(qs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The first profile sends one phrase per language; the background pass all of them (the first ones cached)."""
    seen: set[str] = set()
    out = []
    for lang, q in qs:
        if lang == "custom" or lang not in seen:
            seen.add(lang)
            out.append((lang, q))
    return out


def _budget(plan: sp.Plan, fast: bool) -> tuple[int, int]:
    """Posts per theme and clips per theme: the first profile gets a short run, the background pass the full one."""
    if fast:
        return max(3, plan.posts_per_intent // 2), min(2, plan.videos_per_intent)
    return plan.posts_per_intent, plan.videos_per_intent


# ---------- TikTok ----------
async def tiktok(uni: University, plan: sp.Plan, fast: bool = False) -> list[PhotoCandidate]:
    up = await sp.load_uni(uni.qid)
    forms, iso, center = _context(uni, up)
    intents = [i for i in sp.enabled(plan, "tiktok") if i.fast or not fast]
    if not intents:
        return []
    disc = await known(uni, forms, plan, fast)
    jobs: list[tuple[str, str, str]] = []          # (endpoint, intent, query)
    for i in intents:
        qs = _first_per_lang(sp.queries(plan, i, uni, "tiktok", up.names)) if fast else             sp.queries(plan, i, uni, "tiktok", up.names)
        jobs += [("keyword", i.key, q) for _, q in qs]
        if i.key in ("atmosphere", "campus") and qs:
            jobs.append(("top", i.key, qs[0][1]))      # the only search that returns the Photo tab's slideshows
    keys = {i.key for i in intents}
    jobs += [("keyword", s["intent"], s["query"]) for s in disc.get("suggestions", []) if s["intent"] in keys]
    en_tag = _flat(uni.names.get("en") or "")
    main_tag = next((t["tag"] for t in disc.get("ig_tags", []) if t["tag"].isascii()), None) or \
        (en_tag if 8 <= len(en_tag) <= 30 else None)
    if main_tag and "atmosphere" in keys:
        jobs.append(("hashtag", "atmosphere", main_tag))
    jobs = list(dict.fromkeys(jobs))

    def call(kind: str, q: str):
        if kind == "keyword":
            return _sc("/v1/tiktok/search/keyword", _max_age=SEARCH_TTL, query=q)
        if kind == "top":
            return _sc("/v1/tiktok/search/top", _max_age=SEARCH_TTL, query=q)
        return _sc("/v1/tiktok/search/hashtag", _max_age=SEARCH_TTL, hashtag=q)

    answers = await video_frames.until_deadline([call(k, q) for k, _, q in jobs], margin=4.0 if fast else 8.0)
    posts: list[Post] = []
    by_key = {i.key: i for i in intents}
    for (kind, ik, q), j in zip(jobs, answers):
        items = (j or {}).get("search_item_list") or (j or {}).get("items") or (j or {}).get("aweme_list") or []
        for raw in items:
            p = _tt_post(raw, ik, q)
            if p:
                score(p, forms, iso, center, by_key.get(ik))
                posts.append(p)
    per_intent, max_videos = _budget(plan, fast)
    picked = _select(posts, plan, per_intent)
    log.info("tiktok %s: %d queries, %d posts, %d picked (%s)", uni.qid, len(jobs), len(posts), len(picked),
             {k: sum(p.intent == k for p in picked) for k in keys})
    return await _media(picked, max_videos, uni)


# ---------- Instagram ----------
async def instagram(uni: University, plan: sp.Plan, fast: bool = False) -> list[PhotoCandidate]:
    """Hashtags people use for the university (found by Instagram's own search, with their post counts) and the
    Popular page for the theme queries (it exists only for queries people search a lot: most return nothing)."""
    up = await sp.load_uni(uni.qid)
    forms, iso, center = _context(uni, up)
    intents = [i for i in sp.enabled(plan, "instagram") if i.fast or not fast]
    if not intents:
        return []
    disc = await known(uni, forms, plan, fast)
    keys = {i.key for i in intents}
    jobs: list[tuple[str, str, str]] = []
    tags = [t for t in disc.get("ig_tags", []) if t["intent"] in keys][:(2 if fast else 5)]
    en_tag = _flat(uni.names.get("en") or "")
    if not tags and 8 <= len(en_tag) <= 30:
        tags = [{"tag": en_tag, "intent": "atmosphere"}]    # discovery not in yet: the English name run together
    jobs += [("hashtag", t["intent"], t["tag"]) for t in tags]
    en = uni.names.get("en") or uni.name
    jobs.append(("popular", "atmosphere" if "atmosphere" in keys else intents[0].key, en))
    for i in intents:
        qs = [q for lang, q in sp.queries(plan, i, uni, "instagram", up.names) if lang in ("en", "custom")]
        jobs += [("popular", i.key, q) for q in qs[:1]]
    jobs = list(dict.fromkeys(jobs))

    def call(kind: str, q: str):
        if kind == "hashtag":
            return _sc("/v1/instagram/search/hashtag", _max_age=SEARCH_TTL, hashtag=q, media_type="all")
        return _sc("/v1/instagram/search/popular", _max_age=SEARCH_TTL, query=q)

    answers = await video_frames.until_deadline([call(k, q) for k, _, q in jobs], margin=4.0 if fast else 8.0)
    by_key = {i.key: i for i in intents}
    posts: list[Post] = []
    for (kind, ik, q), j in zip(jobs, answers):
        for raw in (j or {}).get("posts") or []:
            p = _ig_post(raw, ik, q)
            if p:
                score(p, forms, iso, center, by_key.get(ik))
                posts.append(p)
    per_intent, max_videos = _budget(plan, fast)
    picked = _select(posts, plan, per_intent)
    log.info("instagram %s: %d queries, %d posts, %d picked", uni.qid, len(jobs), len(posts), len(picked))
    return await _media(picked, max_videos, uni)


async def instagram_accounts(uni: University, plan: sp.Plan, fast: bool = False) -> list[PhotoCandidate]:
    """Posts of the university's own smaller accounts - the library, the dorms, clubs, teams, the student magazine -
    found by name in Instagram's search. The press office posts announcements; these post the rooms and the people."""
    if not plan.use_accounts:
        return []
    up = await sp.load_uni(uni.qid)
    forms, iso, center = _context(uni, up)
    disc = await discover(uni, forms, plan)
    live = {i.key: i for i in plan.intents if i.enabled and "instagram" in i.platforms}
    accounts = [a for a in disc.get("ig_accounts", []) if a["intent"] in live]
    # one account per theme first, then the rest: the library and a dorm before a fourth sports club
    order: list[dict] = []
    for a in accounts:
        if a["intent"] not in {x["intent"] for x in order}:
            order.append(a)
    order += [a for a in accounts if a not in order]
    order = order[:(3 if fast else 8)]
    answers = await video_frames.until_deadline(
        [_sc("/v2/instagram/user/posts", _max_age=SEARCH_TTL, handle=a["username"]) for a in order], margin=8.0)
    posts: list[Post] = []
    for a, j in zip(order, answers):
        for raw in (j or {}).get("items") or []:
            p = _ig_post(raw, a["intent"], f"@{a['username']}", source="instagram_accounts")
            if p:
                p.affiliated = True
                p.author, p.author_name = a["username"], a.get("full_name") or ""
                score(p, forms, iso, center, live.get(a["intent"]))
                posts.append(p)
    per_intent, max_videos = _budget(plan, fast)
    picked = _select(posts, plan, per_intent)
    log.info("instagram accounts %s: %s -> %d picked", uni.qid, [a["username"] for a in order], len(picked))
    return await _media(picked, max_videos, uni)
