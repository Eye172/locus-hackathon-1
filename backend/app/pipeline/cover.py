"""The cover: the five photos a university's page opens with, chosen by a photo editor, not by a formula.

The inspector judges one photo at a time (is it this university, what does it show, is it informative) at low
resolution. That says nothing about which photo is the one to open the page with: the audit of 19 Sep found an atrium
with palm trees on Nazarbayev University's cover while its domed main building at sunset sat among the candidates,
AUCA opening with a staircase interior next to a group selfie and two classrooms, a staff group photo as KBTU's
"library". So a second, comparative look: the best-ranked candidates side by side at 640 px, one request, and a model
told to act as the editor of a university guide - rate the view (aerial, the campus from outside, a facade, a yard, a
big interior, a room, people, a detail), how much of the campus it shows, how beautiful it is, whether people pose -
then pick the cover (the campus from outside, beautifully) and four tiles (different sides of life, the place visible).

The answer is checked in code (the cover must be an outside view, no posed photos on the tiles), cached per set of
candidates (kv bucket "cover") and applied to the profile: `Profile.cover` lists the ids in order, `Photo.look` keeps
the ratings. About 25 images at 560 tokens: a fraction of a cent per university.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import httpx
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from typing import Literal

from .. import cache, http
from ..config import settings
from ..models import Look, Photo, University
from . import ai_inspector as ai

log = logging.getLogger("campuslens.cover")

VERSION = 2                   # bump when the prompt or the selection changes: cached picks are then asked again
SIDE = 640
N_OUTSIDE = 12                # campus views sent to the editor
N_LIFE = 13                   # the other sides of life, a few per category
PER_CATEGORY = 3
DUP_COSINE = 0.9              # two shots this alike (CLIP) are one candidate
DUP_HAMMING = 10              # the same by 64-bit perceptual hash
BAD_FLAGS = {"banner", "text", "logo", "screenshot", "collage", "illustration", "portrait", "official_meeting", "crop",
             "stock"}
TRUSTED = {"official", "wikipedia", "commons_cat", "commons_depicts", "commons_geo", "places", "map_review"}
OUTSIDE = {"aerial", "exterior_wide", "exterior_building", "courtyard"}
LIFE_ORDER = ["library", "student_life", "dormitory", "sports", "lab", "classroom", "campus"]

PROMPT = """You are the photo editor of a guide to universities. The page of {name} ({city}) opens with five photos: one large cover and four smaller tiles. Choose them from the candidates 1..{k} (source and caption given for each).
For every candidate return:
- view: aerial (from a drone or a height) | exterior_wide (the campus or several of its buildings from outside) | exterior_building (one building's facade) | courtyard (a square, yard or park between the buildings) | interior_space (an atrium, a hall, a reading room, a gym - a large space) | room (a classroom, a lab, a dorm room, an office, a canteen corner) | people (people fill the frame, the place is barely visible) | detail (a sign, an object, a close-up).
- campus 0-3: how much of THIS university's campus it shows; 3 = the campus or its main building, recognisable at a glance.
- beauty 0-3: 3 = worth a magazine cover: good light (golden hour, blue sky, lit windows at dusk), sharp, level, well composed, the place looks alive and inviting; 2 = a good clean photo; 1 = ordinary: flat grey light, an awkward angle, cars or clutter in front, slightly soft; 0 = dark, blurred, tilted, overexposed, a heavy filter, a watermark, text or stickers over it, a screenshot, a phone video frame with interface elements.
- posed: true when people pose for the camera (a group photo, a selfie, officials in a row).
Then choose:
- cover: the cover's number. It shows the campus or its main building from outside (aerial, exterior_wide, exterior_building, courtyard), beautifully - the one photo that makes a student want to go there. Only if no candidate shows the outside well: the most beautiful view of the place itself - never people, never a room with a screen or a presentation.
- tiles: four more numbers: different sides of life there - a characteristic interior (the library, an atrium), student life where the place is visible, a dorm, sport, a lab, another view of the campus in other light or season. Each a different place; no posed photos, portraits, officials, screens or presentations; nothing with beauty below 2 unless there is nothing else."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "n": {"type": "INTEGER"},
                "view": {"type": "STRING", "enum": ["aerial", "exterior_wide", "exterior_building", "courtyard",
                                                   "interior_space", "room", "people", "detail"]},
                "campus": {"type": "INTEGER"},
                "beauty": {"type": "INTEGER"},
                "posed": {"type": "BOOLEAN"},
            },
            "required": ["n", "view", "campus", "beauty", "posed"],
        }},
        "cover": {"type": "INTEGER"},
        "tiles": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
    "required": ["items", "cover", "tiles"],
}


class _Item(BaseModel):
    n: int
    view: Literal["aerial", "exterior_wide", "exterior_building", "courtyard", "interior_space", "room", "people",
                  "detail"]
    campus: int = Field(ge=0, le=3)
    beauty: int = Field(ge=0, le=3)
    posed: bool = False


class _Answer(BaseModel):
    items: list[_Item]
    cover: int = 0
    tiles: list[int] = Field(default_factory=list)


class Pick(BaseModel):
    """What is cached and applied: the ids in order (cover first) and the ratings of every candidate."""
    order: list[str] = Field(default_factory=list)
    looks: dict[str, Look] = Field(default_factory=dict)
    model: str | None = None


# ---------- candidates ----------
def _thumb(p: Photo):
    return settings.thumbs_dir / f"{p.id}.jpg"


def _eligible(p: Photo) -> bool:
    if p.rejected or p.level == "unverified" or p.category == "city" or p.outdated:
        return False
    if p.width < 640 or p.width / max(1, p.height) < 1.1:      # the header is wide: portrait frames crop badly
        return False
    if p.ai is not None:
        if p.ai.place != "this_university" or p.ai.rel < 2 or p.ai.q < 2 or BAD_FLAGS.intersection(p.ai.flags):
            return False
    elif p.confidence < 0.8 or p.source not in TRUSTED:        # no AI look: only a trusted, well-attested photo
        return False
    return p.junk_score < 0.35 and _thumb(p).exists()


def _pre(p: Photo) -> float:
    """Ranking before the editor sees them: what the pipeline already knows."""
    q = p.ai.q if p.ai else 2
    ratio = p.width / max(1, p.height)
    return (p.confidence + 0.25 * q + (0.3 if 1.3 <= ratio <= 2.2 else 0.0) + 0.25 * min(1.0, p.width / 1600)
            + (0.15 if p.source in TRUSTED else 0.0) + (0.1 if p.level == "verified" else 0.0) - p.junk_score)


def candidates(photos: list[Photo], embs: dict[str, np.ndarray] | None = None) -> list[Photo]:
    """Campus views first (the cover comes from them), then a few of each other side of life - near-duplicates
    (CLIP) dropped, so the editor compares different shots."""
    embs = embs or {}
    pool = sorted((p for p in photos if _eligible(p)), key=_pre, reverse=True)
    chosen: list[Photo] = []
    vecs: list[np.ndarray] = []

    def take(p: Photo) -> bool:
        e = embs.get(p.id)
        if e is not None and any(float(np.dot(e, v)) >= DUP_COSINE for v in vecs):
            return False
        # a saved profile has no embeddings: its perceptual hashes catch the same shot
        if p.phash and any(c.phash and len(c.phash) == len(p.phash) and
                           bin(int(c.phash, 16) ^ int(p.phash, 16)).count("1") <= DUP_HAMMING for c in chosen):
            return False
        chosen.append(p)
        if e is not None:
            vecs.append(e)
        return True

    n = 0
    for p in pool:
        if n >= N_OUTSIDE:
            break
        if p.category == "campus" and take(p):
            n += 1
    per: dict[str, int] = {}
    life = 0
    for c in LIFE_ORDER:                 # one of each first, then the rest round by round
        for p in pool:
            if life >= N_LIFE:
                break
            if p.category == c and p not in chosen and per.get(c, 0) < 1 and take(p):
                per[c] = per.get(c, 0) + 1
                life += 1
    for p in pool:
        if life >= N_LIFE:
            break
        if p not in chosen and p.category != "campus" and per.get(p.category, 0) < PER_CATEGORY and take(p):
            per[p.category] = per.get(p.category, 0) + 1
            life += 1
    return chosen


def key(qid: str, cands: list[Photo]) -> str | None:
    if len(cands) < 3:
        return None
    h = hashlib.sha1(("|".join(sorted(p.id for p in cands)) + f"|v{VERSION}").encode()).hexdigest()[:16]
    return f"{qid}:{h}"


async def cached(k: str) -> Pick | None:
    hit = await cache.kv_get("cover", k)
    return Pick.model_validate(hit) if hit else None


# ---------- the editor ----------
async def _ask(body: dict, timeout: float) -> tuple[dict, str]:
    """Vertex first (with a copy after 12 s: an answer takes ~5-9 s, a stuck one never comes), then the AI Studio key."""
    for k, key_, model in ai.gemini_routes():
        if k == 0 and model not in settings.vertex_inspect_models:
            continue
        try:
            url, hdrs = await ai._endpoint(k, key_, model)
            if k == 0:
                gen = body["generationConfig"]
                variants = ai.vertex_variants(model, lambda m: {**body, "generationConfig": {
                    **gen, "thinkingConfig": ai.thinking(m, gen.get("thinkingConfig", {}).get("thinkingLevel", "low"))}})
                r = await ai._vertex_hedged(variants, None, hdrs, timeout, hedge_s=12.0)
            else:
                r = await http.post(url, json=body, headers=hdrs, timeout=timeout)
        except (httpx.TransportError, OSError) as e:
            log.info("cover: %s on route %d failed: %r", model, k, e)
            continue
        if r.status_code == 200:
            return r.json(), getattr(r, "model_used", "") or model
        if r.status_code == 429 and k and ai._daily_quota(r):
            ai._spent[(k, model)] = time.monotonic() + ai.SPENT_S
        log.info("cover: %s on route %d answered %d", model, k, r.status_code)
    raise RuntimeError("no model answered")


def _decide(cands: list[Photo], ans: _Answer) -> list[str]:
    """The editor's choice, checked: the cover is an outside view that is at least good (else the best such by the
    ratings); tiles are not posed, not people-filling, not ugly, not two of the same place."""
    looks = {it.n: it for it in ans.items if 1 <= it.n <= len(cands)}

    def ok(n: int) -> bool:
        return n in looks

    def score(n: int) -> float:
        it = looks[n]
        return it.beauty * 1.0 + it.campus * 0.7 + (0.8 if it.view in OUTSIDE else 0.0) - (2.0 if it.posed else 0.0) \
            - (1.5 if it.view in ("people", "detail") else 0.0)

    cover = ans.cover if ok(ans.cover) else None
    if cover is None or looks[cover].view not in OUTSIDE or looks[cover].beauty < 2 or looks[cover].posed:
        outside = [n for n in looks if looks[n].view in OUTSIDE and not looks[n].posed]
        good_outside = [n for n in outside if looks[n].beauty >= 2]
        # a large space inside (an atrium, a reading room) that is beautiful beats a dull facade (AUCA: a grey
        # overcast building, beauty 1, against its bright atrium, beauty 3)
        inside = [n for n in looks if looks[n].view == "interior_space" and looks[n].beauty >= 3 and not looks[n].posed]
        if good_outside:
            cover = max(good_outside, key=score)
        elif cover is not None and looks[cover].view == "interior_space" and looks[cover].beauty >= 2 and not looks[cover].posed:
            pass
        elif inside:
            cover = max(inside, key=score)
        elif outside:
            cover = max(outside, key=score)
    if cover is None:
        cover = max(looks, key=score, default=None)
    if cover is None:
        return []
    order = [cover]
    cats: dict[str, int] = {cands[cover - 1].category: 1}

    def fits(n: int, strict: bool) -> bool:
        it = looks[n]
        if n in order or it.posed or it.view in ("people", "detail"):
            return False
        if strict and (it.beauty < 2 or cats.get(cands[n - 1].category, 0) >= (2 if cands[n - 1].category == "campus" else 1)):
            return False
        return True

    for n in ans.tiles:
        if ok(n) and len(order) < 5 and fits(n, strict=True):
            order.append(n)
            cats[cands[n - 1].category] = cats.get(cands[n - 1].category, 0) + 1
    for strict in (True, False):          # the editor gave fewer than four usable: fill by the ratings
        for n in sorted(looks, key=score, reverse=True):
            if len(order) >= 5:
                break
            if fits(n, strict):
                order.append(n)
                cats[cands[n - 1].category] = cats.get(cands[n - 1].category, 0) + 1
    return [cands[n - 1].id for n in order]


async def pick(uni: University, cands: list[Photo], k: str, timeout: float = 35.0, tries: int = 3) -> Pick | None:
    """One editor request over the candidates; the result cached under `k`. None when no model answered.
    Vertex has minutes when nothing gets through (19 Sep: three copies of the same request hung 60 s, a minute later
    all three answered in 4.5 s), so a failed ask is repeated after a pause, not at once."""
    if len(cands) < 3 or not ai.gemini_routes():
        return None
    parts: list[dict] = [{"text": PROMPT.format(name=uni.names.get("en") or uni.name, city=uni.city or "its city",
                                                k=len(cands))}]
    for n, p in enumerate(cands, 1):
        try:
            im = Image.open(_thumb(p)).convert("RGB")
        except OSError:
            im = Image.new("RGB", (64, 48))
        cap = (p.title or "").replace("\n", " ")[:70]
        parts += [{"text": f"{n}: {p.source_label}; {p.category}; {cap}"},
                  {"inline_data": {"mime_type": "image/jpeg", "data": ai.jpeg_b64(im, SIDE)}}]
    gen = {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2,
           "mediaResolution": "MEDIA_RESOLUTION_MEDIUM", "thinkingConfig": {"thinkingLevel": "low"}}
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
    t = time.monotonic()
    ans, model = None, None
    for attempt in range(tries):
        try:
            j, model = await _ask(body, timeout)
            ans = _Answer.model_validate_json(j["candidates"][0]["content"]["parts"][0]["text"])
            break
        except Exception as e:  # noqa: BLE001
            log.warning("cover %s: editor failed (try %d): %r", uni.qid, attempt + 1, e)
            if attempt + 1 < tries:
                await asyncio.sleep(20.0 * (attempt + 1))
    if ans is None:
        return None
    order = _decide(cands, ans)
    res = Pick(order=order, model=model, looks={
        cands[it.n - 1].id: Look(view=it.view, campus=it.campus, beauty=it.beauty, posed=it.posed)
        for it in ans.items if 1 <= it.n <= len(cands)})
    try:
        await cache.kv_set("cover", k, res.model_dump())
    except Exception as e:  # noqa: BLE001  (a busy database: the pick itself is still good)
        log.warning("cover %s: not cached: %r", uni.qid, e)
    log.info("cover %s: %d candidates -> %s in %.1f s (%s)", uni.qid, len(cands),
             [(next(p for p in cands if p.id == i).category, res.looks[i].view, res.looks[i].beauty) for i in order],
             time.monotonic() - t, model)
    return res


def apply(res: Pick | None, photos: list[Photo]) -> list[str]:
    """Ratings onto the photos, and the cover ids that are still in the profile (in order)."""
    if not res:
        return []
    by_id = {p.id: p for p in photos}
    for pid, look in res.looks.items():
        if pid in by_id:
            by_id[pid].look = look
    return [pid for pid in res.order if pid in by_id and not by_id[pid].rejected]
