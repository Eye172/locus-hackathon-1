"""The collage: what this university is like, theme by theme - atmosphere, a day in the life, the campus, the dorms,
events, classes, the library, labs, sports, food (the themes of the search plan, pipeline/search_plan.py).

Every checked photo that is not rejected competes for a place. How well it fits a theme:
- it was found by that theme's search and the AI put it in a category the theme accepts (a dorm-tour frame the AI
  calls "dormitory") - the best fit;
- it was found by that theme's search but shows something else (a "dorm" query returned the main building) - weak;
- it came from elsewhere but its category belongs to the theme (a Google Maps review photo of a dorm room) - good.
How good it is: the confidence score, the AI's usefulness, how sure it is that this is this university; a portrait,
a poster, a screenshot or an official meeting goes to the back. The pairs are taken best first; a theme stops at its
target; one post gives at most two frames, one author at most three photos per theme, and a near-copy of a photo
already chosen (perceptual hash) is skipped. Every photo sits in at most one theme.
"""
from __future__ import annotations

from collections import Counter

from ..models import CollageSection, Photo
from . import search_plan as sp

# posted by people who were there, not by the press office
AUTHENTIC = {"map_review", "vk_geo", "instagram_search", "instagram_accounts", "instagram_tagged", "tiktok_search",
             "tiktok_top", "tiktok_hashtag", "tiktok", "youtube_search", "mapillary", "places"}
PHASH_NEAR = 10


def quality(p: Photo) -> float:
    from .curate import looks
    q = p.quality / 3 if p.quality is not None else 0.5
    s = 0.45 * p.confidence + 0.35 * q + looks(p)
    flags = set(p.ai.flags) if p.ai else set()
    if p.ai:
        s += 0.1 * p.ai.rel / 3
    if "portrait" in flags:
        s -= 0.25
    if flags & {"banner", "text"}:
        s -= 0.2       # a collage is looked at, not read: a title card or a caption across the frame goes back
    if flags & {"screenshot", "collage", "logo", "illustration", "stock"}:
        s -= 0.3
    if "official_meeting" in flags:
        s -= 0.2
    if p.outdated:
        s -= 0.1
    if min(p.width, p.height) >= 700:
        s += 0.03
    if p.level == "verified":
        s += 0.03
    return s


# themes about what it feels like to be there: what people posted counts, a press photo of the facade does not
LIVED = {"atmosphere", "day_in_life", "events"}


def fit(p: Photo, intent: sp.Intent) -> float:
    cats = intent.categories
    authentic = bool(AUTHENTIC.intersection(p.sources or [p.source]))
    if intent.strict and p.intent != intent.key:
        return 0.0
    if p.intent == intent.key:
        f = 1.0 if (not cats or p.category in cats) else 0.45
    elif cats and p.category in cats:
        # a campus exterior fits "events" or "food" only by accident; the campus theme wants it
        f = 0.5 if p.category == "campus" and intent.key != "campus" else 0.75
    elif not cats:   # atmosphere: anything real on the campus
        f = 0.45 if p.category in ("campus", "student_life") else 0.2
    else:
        return 0.0
    if intent.key in LIVED:
        f *= 1.0 if authentic else 0.45
    return f


def _near(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count() <= PHASH_NEAR
    except ValueError:
        return False


# a collage is a picture of the place: an announcement, a screenshot or a logo is not, whatever its score
NOT_A_VIEW = {"banner", "screenshot", "logo", "illustration", "stock", "collage", "portrait"}


def build(photos: list[Photo], plan: sp.Plan) -> list[CollageSection]:
    pool = [p for p in photos if not p.rejected and p.level in ("verified", "likely") and p.category != "city"
            and not (p.ai and NOT_A_VIEW.intersection(p.ai.flags)) and (p.quality is None or p.quality >= 2)]
    intents = [i for i in plan.intents if i.enabled and i.target > 0]
    pairs = sorted(((f * (0.5 + quality(p)), p.id, p, i) for p in pool for i in intents if (f := fit(p, i)) > 0),
                   key=lambda x: (-x[0], x[1]))
    chosen: dict[str, list[Photo]] = {i.key: [] for i in intents}
    used: set[str] = set()
    per_page: Counter = Counter()
    per_author: Counter = Counter()
    for _, _, p, i in pairs:
        lst = chosen[i.key]
        if p.id in used or len(lst) >= i.target:
            continue
        if per_page[p.page_url] >= 2 or (p.author and per_author[(i.key, p.author)] >= 3):
            continue
        if any(_near(p.phash, q.phash) for sec in chosen.values() for q in sec):
            continue
        lst.append(p)
        used.add(p.id)
        per_page[p.page_url] += 1
        if p.author:
            per_author[(i.key, p.author)] += 1
    by_id = {p.id: p for p in photos}
    for p in photos:
        p.collage = None
    out: list[CollageSection] = []
    for i in intents:
        for p in chosen[i.key]:
            by_id[p.id].collage = i.key
        out.append(CollageSection(key=i.key, label=i.label, target=i.target, photos=[p.id for p in chosen[i.key]],
                                  found=sum(1 for p in pool if fit(p, i) >= 0.7)))
    return out


def plan_summary(plan: sp.Plan, uni) -> dict:
    """What was searched, for the profile page: every theme with its queries per network."""
    return {"hash": sp.plan_hash(plan),
            "intents": [{"key": i.key, "label": i.label, "target": i.target, "enabled": i.enabled,
                         "queries": {pl: [q for _, q in sp.queries(plan, i, uni, pl)] for pl in i.platforms
                                     if pl != "maps"}}
                        for i in plan.intents]}
