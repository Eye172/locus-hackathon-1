"""What CampusLense looks for: themes ("intents") a student would post about, not keywords a librarian would type.

"<university> atmosphere", "a day in the life of a <university> student", "<university> dorm tour", "<university>
events" - each theme is searched the way people post it, on the networks where they post it, in English and in the
language the university's own students write in. Every theme has
- phrases per language with {name} in them (edited on the settings page; a language without its own phrases falls
  back to the word for the theme in langs.CONCEPTS);
- the networks it is searched on (tiktok, instagram, google images, google maps, youtube);
- the words that tie a post's caption to it and the words in an account's name that make that account's posts belong
  to it (a university's library account → library);
- the AI categories that count for it and a soft target: the collage (pipeline/collage.py) tries to give each theme
  about that many photos, so all of them together show what the place is like.

The defaults are below. Edits from the settings page are saved to data/search_plan.json; a university can have its
own extra queries on top (kv bucket "plan"). A build reads the plan when it starts, so a change applies to the next
opening of a profile.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .. import cache
from ..config import settings
from ..models import University

PLATFORMS = ["tiktok", "instagram", "google", "maps", "youtube"]


class Intent(BaseModel):
    key: str
    label: str
    enabled: bool = True
    target: int = 8                                   # soft share of the collage
    categories: list[str] = Field(default_factory=list)   # AI categories that belong here (empty = any)
    platforms: list[str] = Field(default_factory=lambda: list(PLATFORMS))
    phrases: dict[str, list[str]] = Field(default_factory=dict)   # lang -> ["{name} dorm tour", ...]
    concept: str | None = None        # langs.CONCEPTS key used for languages without phrases
    caption_words: list[str] = Field(default_factory=list)   # a caption with one of these is about this theme
    account_words: list[str] = Field(default_factory=list)   # an affiliated account named like this posts this theme
    maps: list[str] = Field(default_factory=list)            # Google Maps places near the campus: "<name> <word>"
    fast: bool = False                # searched already for the first profile (the rest in the background pass)
    strict: bool = False              # only photos found by this theme's own search (no AI category for "food")


class CustomQuery(BaseModel):
    intent: str
    query: str                        # may contain {name}; without it the text is searched as typed
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "instagram", "google"])


class Plan(BaseModel):
    intents: list[Intent]
    languages: Literal["both", "en", "local"] = "both"
    posts_per_intent: int = 6         # posts opened per theme and network (after ranking all that came back)
    videos_per_intent: int = 5        # of them, clips cut into frames (a clip not opened gives only its cover)
    phrases_per_intent: int = 2       # phrases per language actually sent (the first ones)
    min_relevance: float = 1.5        # a post below this score is not opened at all (social_search.score)
    use_accounts: bool = True         # posts of the university's clubs, library, dorm accounts found by name
    use_suggestions: bool = True      # TikTok's own autocomplete ("<name> aesthetic", "<name> dorm") as extra queries
    custom: list[CustomQuery] = Field(default_factory=list)   # extra queries for every university


class UniPlan(BaseModel):
    """Per-university additions: extra queries and themes switched off for this one."""
    custom: list[CustomQuery] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)    # extra names people use for it (a new brand, a nickname)


def _i(key, label, target, cats, phrases, concept=None, caption=(), accounts=(), maps=(), fast=False,
       platforms=None, strict=False) -> Intent:
    return Intent(key=key, label=label, target=target, categories=list(cats), phrases=phrases, concept=concept,
                  caption_words=list(caption), account_words=list(accounts), maps=list(maps), fast=fast, strict=strict,
                  platforms=platforms or [p for p in PLATFORMS if p != "youtube"])   # 100 quota units a search


DEFAULT_INTENTS: list[Intent] = [
    _i("atmosphere", "Атмосфера", 15, [],
       {"en": ["{name} aesthetic", "{name} atmosphere", "{name} vibes"],
        "ru": ["атмосфера {name}", "{name} эстетика"]},
       concept="atmosphere", caption=["atmosphere", "aesthetic", "vibes", "атмосфер", "эстетик", "вайб", "edit"],
       fast=True, platforms=["tiktok", "instagram", "google"]),
    # the campus from above: drone clips and aerial photos - the one view that shows the whole place at once, and the
    # cover's best source (pipeline/cover.py). YouTube's own stills from inside a drone video are aerial frames with no
    # title card on them
    _i("aerial", "Кампус с высоты", 6, ["campus"],
       {"en": ["{name} drone", "{name} aerial view"], "ru": ["{name} с высоты", "{name} аэросъемка"]},
       concept="aerial", caption=["drone", "aerial", "from above", "дрон", "с высоты", "аэросъем", "квадрокоптер"],
       fast=True, platforms=["youtube", "google", "tiktok"], strict=True),
    _i("day_in_life", "Один день из жизни студента", 12, ["student_life", "classroom", "library", "campus", "dormitory"],
       {"en": ["day in the life {name} student", "{name} vlog"],
        "ru": ["один день из жизни студента {name}", "{name} влог"]},
       concept="student_life", caption=["day in", "vlog", "влог", "один день", "студенческ", "student life"],
       fast=True, platforms=["tiktok", "instagram", "youtube"]),
    _i("campus", "Кампус", 12, ["campus"],
       {"en": ["{name} campus", "{name} campus tour"],
        "ru": ["{name} кампус", "обзор {name}"]},
       concept="campus", caption=["campus", "кампус", "tour", "обзор", "корпус", "building"], fast=True,
       platforms=list(PLATFORMS)),
    _i("dorm", "Общежития", 10, ["dormitory"],
       {"en": ["{name} dorm", "{name} dorm tour", "{name} dorm room"],
        "ru": ["{name} общежитие", "общага {name}", "рум тур {name}"]},
       concept="dormitory", caption=["dorm", "residence", "общежит", "общаг", "жатақхана", "room tour", "рум тур"],
       accounts=["dorm", "residence", "общежит", "жатақхана", "housing"], maps=["dormitory", "общежитие"], fast=True,
       platforms=list(PLATFORMS)),
    _i("events", "Ивенты", 10, ["student_life", "sports"],
       {"en": ["{name} event", "{name} fest", "{name} graduation"],
        "ru": ["{name} мероприятие", "{name} посвящение", "{name} выпускной"]},
       caption=["event", "fest", "graduation", "concert", "party", "club", "мероприят", "посвящ", "выпускн",
                "концерт", "фест", "клуб"],
       accounts=["club", "society", "union", "council", "клуб", "совет", "enactus", "tedx", "fest"],
       platforms=["tiktok", "instagram", "google"]),
    _i("classes", "Учёба и аудитории", 6, ["classroom", "lab"],
       {"en": ["{name} lecture", "{name} class"], "ru": ["{name} пара", "{name} аудитория"]},
       concept="classroom", caption=["lecture", "classroom", "in class", "пара", "лекци", "аудитор", "семинар",
                                      "seminar", "study session"]),
    _i("library", "Библиотека", 6, ["library"],
       {"en": ["{name} library"], "ru": ["{name} библиотека"]},
       concept="library", caption=["library", "библиотек", "кітапхана"], accounts=["library", "библиотек", "кітапхана"],
       maps=["library", "библиотека"]),
    _i("labs", "Лаборатории", 6, ["lab"],
       {"en": ["{name} lab"], "ru": ["{name} лаборатория"]},
       concept="lab", caption=["lab", "лаборатор", "research", "makerspace"],
       accounts=["lab", "research", "robot", "rover", "science"]),
    _i("sports", "Спорт", 6, ["sports"],
       {"en": ["{name} gym", "{name} sports"], "ru": ["{name} спортзал", "{name} спорт"]},
       concept="sports", caption=["gym", "sport", "спорт", "стадион", "бассейн", "pool", "football", "basketball"],
       accounts=["sport", "football", "basketball", "volleyball", "judo", "fencing", "спорт", "team"],
       maps=["sports complex", "спорткомплекс"]),
    _i("food", "Еда и кафе", 6, ["student_life", "campus"],
       {"en": ["{name} cafeteria", "{name} food"], "ru": ["{name} столовая", "{name} еда"]},
       concept="canteen", caption=["food", "cafeteria", "canteen", "dining", "coffee", "столов", "кафе", "еда", "асхана"],
       accounts=["cafe", "coffee", "food", "кафе"], maps=["cafeteria", "столовая"], strict=True),
]


def default_plan() -> Plan:
    return Plan(intents=[i.model_copy(deep=True) for i in DEFAULT_INTENTS])


def _path():
    return settings.data_dir / "search_plan.json"


def load() -> Plan:
    """The saved plan, or the defaults. A theme added to the defaults later appears in an old saved plan too."""
    p = _path()
    if not p.exists():
        return default_plan()
    try:
        plan = Plan.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default_plan()
    have = {i.key for i in plan.intents}
    plan.intents += [i.model_copy(deep=True) for i in DEFAULT_INTENTS if i.key not in have]
    return plan


def save(plan: Plan) -> Plan:
    keys = [i.key for i in plan.intents]
    if len(keys) != len(set(keys)):
        raise ValueError("ключи тем должны быть уникальны")
    for i in plan.intents:
        if not re.fullmatch(r"[a-z0-9_]{2,32}", i.key):
            raise ValueError(f"ключ темы «{i.key}»: латиница, цифры, _")
        i.platforms = [x for x in i.platforms if x in PLATFORMS]
        i.target = max(0, min(60, i.target))
    plan.posts_per_intent = max(1, min(30, plan.posts_per_intent))
    plan.videos_per_intent = max(0, min(10, plan.videos_per_intent))
    plan.phrases_per_intent = max(1, min(5, plan.phrases_per_intent))
    _path().write_text(plan.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return plan


def reset() -> Plan:
    _path().unlink(missing_ok=True)
    return default_plan()


async def load_uni(qid: str) -> UniPlan:
    hit = await cache.kv_get("plan", qid)
    return UniPlan.model_validate(hit) if hit else UniPlan()


async def save_uni(qid: str, up: UniPlan) -> UniPlan:
    await cache.kv_set("plan", qid, up.model_dump())
    return up


async def for_university(qid: str) -> Plan:
    """The plan a build of this university runs: the saved plan, its own extra queries and switched-off themes."""
    plan = load()
    up = await load_uni(qid)
    for i in plan.intents:
        if i.key in up.disabled:
            i.enabled = False
    plan.custom = plan.custom + up.custom
    return plan


def plan_hash(plan: Plan) -> str:
    return hashlib.sha1(plan.model_dump_json().encode()).hexdigest()[:10]


# ---------- names people use ----------
_GENERIC = {"university", "institute", "college", "academy", "school", "университет", "институт", "академия",
            "колледж", "university of", "state", "national", "technical"}


def brand_names(uni: University, extra: list[str] | None = None) -> list[str]:
    """Short names in use besides the official one: "Ualikhanov University" for the Kokshetau State University,
    "KBTU" for the Kazakh-British Technical University. From the aliases: two or three words, one of them "University",
    not an older version of the official name."""
    from .sources.social_api import short_names
    out: list[str] = list(extra or [])
    official = {n.lower() for n in [uni.name, *uni.names.values()] if n}
    official_words = {w for n in official for w in re.split(r"[\s\-]+", n)}
    for a in uni.aliases:
        a = a.strip()
        words = a.split()
        if a.lower() in official or not 2 <= len(words) <= 3 or len(a) > 40:
            continue
        # "Кокшетауский университет" is the official name with its distinguishing word dropped - in Kokshetau that
        # is also the name of another university; a brand has a word of its own ("Ualikhanov University")
        if all(w.lower() in official_words for w in words):
            continue
        if re.search(r"university|университет|universität|université|universidad|universit", a, re.I) and \
                not re.search(r"\d", a):
            out.append(a)
    out += short_names(uni)[:1]
    return list(dict.fromkeys(out))


def name_forms(uni: University, extra: list[str] | None = None) -> dict[str, list[str]]:
    """Every form in which a caption can name the university, for matching (lower case):
    full = official names and brands, tags = the same run together (#nazarbayevuniversity), abbr = abbreviations."""
    from .sources.social_api import short_names
    full = [n for n in {uni.name, *uni.names.values(), *brand_names(uni, extra)} if n and len(n) >= 6]
    abbr = [a for a in short_names(uni) if 3 <= len(a) <= 8]
    abbr += [a for a in (extra or []) if len(a) <= 8]
    tag = lambda s: re.sub(r"[^\w]+", "", s.lower(), flags=re.U)
    return {"full": sorted({f.lower() for f in full}, key=len, reverse=True),
            "tags": sorted({tag(f) for f in full if len(tag(f)) >= 8}, key=len, reverse=True),
            "abbr": sorted({a.lower() for a in abbr})}


def queries(plan: Plan, intent: Intent, uni: University, platform: str, extra_names: list[str] | None = None
            ) -> list[tuple[str, str]]:
    """(language, query) pairs of one theme for one network: the first `phrases_per_intent` phrases per language,
    with the name people use in that language; custom queries of the theme added as typed."""
    from .langs import CONCEPTS, local_lang
    from .sources.social_api import short_names
    en_name = uni.names.get("en") or uni.name
    local = local_lang(uni)
    langs: list[str] = []
    if plan.languages in ("both", "en"):
        langs.append("en")
    if plan.languages in ("both", "local") and local != "en":
        langs.append(local)
    brands = brand_names(uni, extra_names)
    out: list[tuple[str, str]] = []
    for lang in langs:
        if lang == "en":
            name = en_name if len(en_name) <= 45 else next((b for b in brands if b.isascii()), en_name)
        else:
            native = uni.names.get(lang) or uni.name
            shorts = [a for a in short_names(uni) if not a.isascii()]
            # "КБТУ общежитие", not "Казахстанско-Британский технический университет общежитие": nobody types that
            name = shorts[0] if shorts and len(native) > 30 else native
        phrases = intent.phrases.get(lang) or []
        if not phrases and intent.concept and lang in CONCEPTS and intent.concept in CONCEPTS[lang]:
            word = CONCEPTS[lang][intent.concept]
            phrases = [f"{{name}} {word}"]
        for ph in phrases[:plan.phrases_per_intent]:
            out.append((lang, ph.replace("{name}", name).strip()))
    for c in plan.custom:
        if c.intent == intent.key and platform in c.platforms:
            out.append(("custom", c.query.replace("{name}", en_name)))
    return list(dict.fromkeys(out))


def intent_of(plan: Plan, key: str | None) -> Intent | None:
    return next((i for i in plan.intents if i.key == key), None) if key else None


def enabled(plan: Plan, platform: str, fast: bool | None = None) -> list[Intent]:
    return [i for i in plan.intents if i.enabled and platform in i.platforms and (fast is None or i.fast == fast)]


def to_json(plan: Plan) -> dict:
    return json.loads(plan.model_dump_json())
