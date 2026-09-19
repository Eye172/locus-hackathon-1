"""The social search tunes itself to each university from what its own results turned out to be.

The search plan (search_plan.py) asks every university the same themes in the same words. What actually brings the
place's atmosphere differs from one to the next and is not known in advance: the hashtag its students really use
(#aucalife, #nulife, a dorm's nickname), the few students and clubs who keep filming the campus, the phrases that
return the campus rather than admission adverts. None of it is typed in by hand - it is read off the verdicts:

- every social post the inspector looked at is counted, per hashtag in its caption, per author and per query that
  found it: seen, and good (the inspector says it is this university, relevant, informative, not a poster);
- a hashtag is "proven" when several good posts by more than one author carry it and it is specific: a tag that shows
  up in the learned sets of three or more universities (#fyp, #student, #university) is generic, whatever it is;
- an author is proven when at least two of their posts were good and most of what they posted about it was;
- a query is dead when it keeps returning posts and none was good.

Within one build, the background pass runs a second round (social_search.learned_round) on the proven tags and authors
found so far. Across builds, the counts are kept per university (kv bucket "learned", the older builds weighing less)
and the next build starts with them: proven tags and authors are searched from the first profile on, dead queries
wait for the background pass.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .. import cache
from ..models import AiVerdict

SOCIAL = ("tiktok", "instagram")
DECAY = 0.7                   # weight of the older builds' counts when a new build is added
TAG_RE = re.compile(r"#(\w{3,40})", re.U)
# tags that belong to the platforms, not to any place - kept out whatever their counts say
PLATFORM_TAGS = {"fyp", "foryou", "foryoupage", "foryourpage", "fy", "fypシ", "viral", "trending", "trend", "reels",
                 "reel", "tiktok", "instagram", "instagood", "photooftheday", "explore", "explorepage", "xyzbca",
                 "recommendations", "capcut", "рек", "рекомендации", "реки", "врек", "рекомендация", "тренд", "тренды",
                 "keşfet", "keşfetteyiz", "keşfetbeniöneçıkar", "parati", "pourtoi", "fürdich", "perte", "voorjou",
                 "dlaciebie", "prosebe", "neked", "おすすめ", "추천", "推荐", "fyi", "viralvideo", "trendingnow"}
GENERIC_UNIS = 3              # a tag learned for this many universities or more is generic
# a hashtag that is only a word for what every university is (in any of the languages people post in) says nothing
# about this one - its good posts came from the university's own tags next to it
GENERIC_WORDS = {"university", "universitet", "universite", "üniversite", "universität", "universitat", "universidad",
                 "università", "universiteit", "uniwersytet", "univerzita", "egyetem", "yliopisto", "universitate",
                 "университет", "університет", "университеті", "college", "institute", "институт", "academy",
                 "академия", "student", "students", "студент", "студенты", "студенттер", "campus", "кампус", "uni",
                 "unilife", "studentlife", "universitylife", "college life", "collegelife", "study", "studying",
                 "education", "образование", "учеба", "ucheba", "大学", "大學", "대학교", "대학", "جامعة"}


class Stat(BaseModel):
    seen: float = 0.0
    good: float = 0.0
    authors: list[str] = Field(default_factory=list)   # for tags: who used it (a few)


class Learned(BaseModel):
    tags: dict[str, Stat] = Field(default_factory=dict)
    authors: dict[str, Stat] = Field(default_factory=dict)    # "tiktok:user" / "instagram:user"
    queries: dict[str, Stat] = Field(default_factory=dict)    # "tiktok:<query>"
    builds: int = 0
    updated: str | None = None


def _platform(source: str) -> str | None:
    return next((p for p in SOCIAL if source.startswith(p)), None)


def good(v: AiVerdict | None) -> bool:
    from .verify import hard_flags
    return bool(v and v.place == "this_university" and v.rel >= 2 and v.q >= 2 and not hard_flags(v))


def mine(fetched, verdicts: dict[str, AiVerdict]) -> Learned:
    """Counts of this build: one post counts once (its frames and slides together), good if any of them is."""
    posts: dict[str, dict] = {}
    for f in fetched:
        for c in [f.cand, *f.extra_sources]:
            plat = _platform(c.source)
            if not plat or not c.page_url:
                continue
            p = posts.setdefault(c.page_url, {"plat": plat, "text": c.text or "", "author": (c.author or "").lstrip("@"),
                                              "queries": set(), "judged": False, "good": False})
            if c.query:
                p["queries"].add(c.query)
            v = verdicts.get(f.id)
            if v is not None:
                p["judged"] = True
                p["good"] = p["good"] or good(v)
    out = Learned()
    tag_authors: dict[str, set[str]] = defaultdict(set)
    for p in posts.values():
        if not p["judged"]:
            continue
        g = 1.0 if p["good"] else 0.0
        for t in {t.lower() for t in TAG_RE.findall(p["text"])}:
            if t in PLATFORM_TAGS or t.isdigit():
                continue
            s = out.tags.setdefault(t, Stat())
            s.seen += 1
            s.good += g
            if p["good"] and p["author"]:
                tag_authors[t].add(f"{p['plat']}:{p['author']}")
        if p["author"]:
            s = out.authors.setdefault(f"{p['plat']}:{p['author']}", Stat())
            s.seen += 1
            s.good += g
        for q in p["queries"]:
            s = out.queries.setdefault(f"{p['plat']}:{q}", Stat())
            s.seen += 1
            s.good += g
    for t, a in tag_authors.items():
        out.tags[t].authors = sorted(a)[:8]
    return out


def merge(old: Learned, new: Learned) -> Learned:
    """The older builds weigh DECAY; the lists are trimmed to what matters most."""
    def mix(a: dict[str, Stat], b: dict[str, Stat], keep: int) -> dict[str, Stat]:
        out = {k: Stat(seen=v.seen * DECAY, good=v.good * DECAY, authors=v.authors) for k, v in a.items()}
        for k, v in b.items():
            s = out.setdefault(k, Stat())
            s.seen += v.seen
            s.good += v.good
            s.authors = sorted(set(s.authors) | set(v.authors))[:8]
        ranked = sorted(out.items(), key=lambda kv: (-kv[1].good, -kv[1].seen))
        return dict(ranked[:keep])
    return Learned(tags=mix(old.tags, new.tags, 80), authors=mix(old.authors, new.authors, 60),
                   queries=mix(old.queries, new.queries, 120), builds=old.builds + 1,
                   updated=datetime.now(timezone.utc).isoformat())


def combined(old: Learned, now: Learned) -> Learned:
    """What is known during a build: the stored counts and this build's so far (not yet saved)."""
    m = merge(old, now)
    m.builds = old.builds
    return m


async def load(qid: str) -> Learned:
    hit = await cache.kv_get("learned", qid)
    return Learned.model_validate(hit) if hit else Learned()


async def generic_tags() -> set[str]:
    hit = await cache.kv_get("learned", "_generic")
    return set(hit or [])


async def record(qid: str, now: Learned) -> Learned:
    """Adds this build's counts to the university's, and updates which tags have turned out generic."""
    merged = merge(await load(qid), now)
    await cache.kv_set("learned", qid, merged.model_dump())
    seen_in: dict[str, list[str]] = (await cache.kv_get("learned", "_tag_unis")) or {}
    for t, s in merged.tags.items():
        if s.good >= 1:
            unis = seen_in.setdefault(t, [])
            if qid not in unis:
                unis.append(qid)
                del unis[:-12]
    await cache.kv_set("learned", "_tag_unis", seen_in)
    await cache.kv_set("learned", "_generic", sorted(t for t, u in seen_in.items() if len(u) >= GENERIC_UNIS))
    return merged


def _flat(s: str) -> str:
    """Letters and digits only, lower case, without accents: Türkiye and #turkiye are the same word."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", "", s, flags=re.U)


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяәғқңөұүһі",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                 "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya", "a", "g", "k", "n", "o", "u", "u",
                 "h", "i"]))


def latin(s: str) -> str:
    """Cyrillic spelled in Latin letters: the profile has the city as "Анкара", students tag it #ankara."""
    return "".join(_CYR.get(ch, ch) for ch in s.lower())


def place_words(uni) -> set[str]:
    """The university's city and country in every spelling the profile has (and in Latin letters), and the words of
    the search themes in every language: a tag made of one of them is about the place or the topic, not about this
    university."""
    from .langs import CONCEPTS
    places = [x for x in (uni.city, uni.country) if x]
    words = {_flat(x) for x in places} | {_flat(latin(x)) for x in places}
    words |= {w[:-1] for w in list(words) if len(w) > 4 and w.endswith("y")}   # Almaty / Almat-
    words |= {w + suffix for w in list(words) for suffix in ("city", "life", "gram")}
    words |= {_flat(w) for d in CONCEPTS.values() for w in d.values()}
    words |= {_flat(w) for w in GENERIC_WORDS}
    return {w for w in words if w}


async def places_of(uni) -> set[str]:
    """place_words plus the city's and the country's names and aliases in every language of the search plan, from
    Wikidata (once per city, cached): the profile has the city in one spelling ("Варшава"), students tag it in theirs
    (#warszawa, #warsaw)."""
    from .langs import CONCEPTS
    from .sources.wikidata import get_entities
    words = place_words(uni)
    ids = [q for q in (getattr(uni, "city_qid", None), getattr(uni, "country_qid", None)) if q]
    if not ids:
        return words
    key = "|".join(ids)
    names = await cache.kv_get("placenames", key)
    if names is None:
        try:
            ents = await get_entities(ids, props="labels|aliases", languages="|".join(sorted(set(CONCEPTS) | {"en"})))
            names = sorted({v["value"] for e in ents.values() for v in (e.get("labels") or {}).values()} |
                           {a["value"] for e in ents.values() for al in (e.get("aliases") or {}).values() for a in al})
            await cache.kv_set("placenames", key, names)
        except Exception:  # noqa: BLE001  (Wikidata down: the profile's own spelling still counts)
            names = []
    flat = {_flat(n) for n in names} | {_flat(latin(n)) for n in names}
    flat |= {w + suffix for w in list(flat) for suffix in ("city", "life", "gram")}
    return words | {w for w in flat if len(w) >= 3}


def anchored(tag: str, forms: dict[str, list[str]] | None) -> bool:
    """The tag carries the university's own name: its name run together, an abbreviation of 3+ letters, a brand."""
    t = _flat(tag)
    return bool(forms) and (any(f and _flat(f) in t for f in forms.get("tags", [])) or
                            any(len(a) >= 3 and _flat(a) in t for a in forms.get("abbr", [])))


def specific(tag: str, uni, forms: dict[str, list[str]] | None = None, places: set[str] | None = None) -> bool:
    """A tag that names this university is specific by construction; any other tag must not be a place or a generic
    word."""
    t = _flat(tag)
    if anchored(tag, forms):
        return True
    words = places if places is not None else place_words(uni)
    return t not in words and not any(t == w + "s" for w in words)


def proven_tags(l: Learned, generic: set[str], skip: set[str] = frozenset(), n: int = 3, uni=None,
                forms: dict[str, list[str]] | None = None, places: set[str] | None = None) -> list[str]:
    ok = [(t, s) for t, s in l.tags.items()
          if t not in generic and t not in skip and t not in PLATFORM_TAGS and s.good >= 2 and s.good / max(1.0, s.seen) >= 0.4
          and len(s.authors) >= 2 and (uni is None or specific(t, uni, forms, places))]
    # the ones that carry the university's name first; of the others (a slogan, a community tag - or a common word
    # that slipped through, #studia) at most one at a time
    ok.sort(key=lambda ts: (not anchored(ts[0], forms), -ts[1].good, -ts[1].seen))
    out, loose = [], 0
    for t, _ in ok:
        if len(out) >= n:
            break
        if not anchored(t, forms):
            if loose:
                continue
            loose += 1
        out.append(t)
    return out


def proven_authors(l: Learned, platform: str, skip: set[str] = frozenset(), n: int = 3) -> list[str]:
    ok = [(a.split(":", 1)[1], s) for a, s in l.authors.items()
          if a.startswith(platform + ":") and a.split(":", 1)[1] not in skip
          and s.good >= 2 and s.good / max(1.0, s.seen) >= 0.5]
    return [a for a, _ in sorted(ok, key=lambda x: (-x[1].good, -x[1].seen))[:n]]


def dead(l: Learned, platform: str, query: str) -> bool:
    """A query that has returned posts over at least two builds and never a good one."""
    s = l.queries.get(f"{platform}:{query}")
    return bool(s and l.builds >= 2 and s.seen >= 6 and s.good == 0)
