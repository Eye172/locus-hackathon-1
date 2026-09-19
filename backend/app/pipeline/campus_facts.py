"""What is on the campus and what it is like: the buildings and what is in them, the dorms and what is in their rooms,
the library, labs and their equipment, computer rooms and Wi-Fi, sports, food, and what students say about it.

Written by an LLM from texts found on the web for this university, now:
- Google web search (Serper) per topic, in the language of the country and in English: the organic results' snippets,
  the answer box, and the text of the first pages themselves (the university's own pages about its dorms and
  library, news articles, student forums);
- the Wikipedia article (the sections about the campus);
- what visitors wrote in their reviews of the university on Google Maps.
Every statement carries the numbers of the sources it comes from, and a topic nothing was found for says so instead
of being filled in. The model also rates each topic from the reviews (good / mixed / poor) when they say something.
Cached for two weeks per university.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .. import cache, http
from ..config import settings
from ..models import University

log = logging.getLogger("campuslens.facts")

SEARCH = "https://google.serper.dev/search"
TTL = 14 * 86400
TOPICS: list[tuple[str, str, dict[str, str]]] = [
    ("buildings", "Корпуса и кампус", {"en": "{name} campus buildings facilities", "ru": "{name} корпуса кампус здания"}),
    ("dorms", "Общежития", {"en": "{name} dormitory rooms", "ru": "{name} общежитие комнаты условия"}),
    ("library", "Библиотека", {"en": "{name} library", "ru": "{name} библиотека"}),
    ("labs", "Лаборатории и оборудование", {"en": "{name} laboratories equipment research centers",
                                            "ru": "{name} лаборатории оборудование"}),
    ("it", "Компьютерные классы и Wi-Fi", {"en": "{name} computer labs wifi", "ru": "{name} компьютерные классы wifi"}),
    ("sports", "Спорт", {"en": "{name} sports complex gym", "ru": "{name} спорткомплекс спортзал"}),
    ("food", "Питание", {"en": "{name} cafeteria dining", "ru": "{name} столовая кафе питание"}),
    ("reviews", "Что говорят студенты", {"en": "{name} student reviews campus life", "ru": "{name} отзывы студентов"}),
]
SKIP = re.compile(r"instagram|tiktok|facebook|youtube|vk\.com|twitter|x\.com|linkedin|pinterest|t\.me|ok\.ru", re.I)
PAGE_CHARS = 2200
WIKI_CHARS = 5000


class FactItem(BaseModel):
    text: str
    sources: list[int] = Field(default_factory=list)


class FactSection(BaseModel):
    key: str
    title: str
    items: list[FactItem] = Field(default_factory=list)
    rating: str = "unknown"          # good | mixed | poor | unknown (from what students wrote)
    rating_note: str = ""


class QuickFact(BaseModel):
    label: str
    value: str
    sources: list[int] = Field(default_factory=list)


class FactSource(BaseModel):
    id: int
    title: str
    url: str
    kind: str                        # web | official | wikipedia | reviews


class _Out(BaseModel):
    summary: str = ""
    sections: list[FactSection] = Field(default_factory=list)
    quick: list[QuickFact] = Field(default_factory=list)


class CampusFacts(BaseModel):
    qid: str
    summary: str = ""
    sections: list[FactSection] = Field(default_factory=list)
    quick: list[QuickFact] = Field(default_factory=list)
    sources: list[FactSource] = Field(default_factory=list)
    model: str = ""
    generated_at: str = ""
    note: str | None = None


# ---------- gathering ----------
async def _serper(q: str, gl: str | None, hl: str) -> dict:
    body = {"q": q, "hl": hl, "num": 8}
    if gl:
        body["gl"] = gl.lower()
    key = f"{q}|{gl}|{hl}"
    hit = await cache.kv_get("websearch", key, max_age_s=7 * 86400)
    if hit is not None:
        return hit
    if http.serper_out():       # the balance is spent (app/http.py)
        return {}
    try:
        r = await http.post(SEARCH, json=body, timeout=12.0,
                            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"})
        r.raise_for_status()
        j = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("serper search %r failed: %r", q, e)
        return {}
    await cache.kv_set("websearch", key, j)
    return j


def _page_text(html: str, words: list[str]) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "svg", "aside"]):
        t.decompose()
    blocks = [" ".join(x.get_text(" ").split()) for x in soup.find_all(["p", "li", "h2", "h3", "td"])]
    blocks = [b for b in blocks if len(b) > 40]
    # the paragraphs about the topic first; a page about the dorms is mostly about the dorms anyway
    hit = [b for b in blocks if any(w in b.lower() for w in words)]
    text = " ".join(hit + [b for b in blocks if b not in hit])
    return text[:PAGE_CHARS]


async def _fetch(url: str, words: list[str]) -> str:
    try:
        r = await http.get(url, timeout=7.0)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        return await asyncio.to_thread(_page_text, r.text, words)
    except Exception:  # noqa: BLE001
        return ""


async def _wiki(uni: University) -> list[tuple[str, str, str]]:
    """(title, url, text) of the Wikipedia articles, campus sections first."""
    out = []
    for lang in ("ru", "en"):
        title = uni.wikipedia.get(lang)
        if not title:
            continue
        try:
            j = await http.get_json(f"https://{lang}.wikipedia.org/w/api.php", params={
                "action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json",
                "redirects": 1}, timeout=8.0)
            page = next(iter((j.get("query") or {}).get("pages", {}).values()), {})
            text = page.get("extract") or ""
        except Exception:  # noqa: BLE001
            continue
        parts = re.split(r"\n(?==+ )|\n{2,}", text)
        key = re.compile(r"кампус|корпус|общежит|библиотек|лаборатор|спорт|campus|facilit|residen|librar|laborator|"
                         r"housing|dormitor|sport|building", re.I)
        parts = [p for p in parts if key.search(p)] + [p for p in parts if not key.search(p)]
        out.append((f"Википедия ({lang}): {title}", f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    " ".join(" ".join(parts).split())[:WIKI_CHARS]))
    return out


async def _reviews(uni: University) -> tuple[str, str, str] | None:
    from .sources import map_reviews
    try:
        pl = await map_reviews.place(uni)
    except Exception:  # noqa: BLE001
        pl = None
    if not pl:
        return None
    reviews = await map_reviews._reviews_page(pl["placeId"], pl.get("hl") or "en", pl.get("gl") or "")
    texts = [" ".join((r.get("snippet") or "").split()) for r in reviews]
    texts = [t for t in texts if len(t) > 30][:30]
    if not texts:
        return None
    return (f"Отзывы на Google Картах: {pl['title']}", f"https://www.google.com/maps/place/?q=place_id:{pl['placeId']}",
            " | ".join(texts)[:3500])


async def gather(uni: University) -> list[dict]:
    """Numbered sources for the prompt: {id, title, url, kind, topic, text}."""
    from .sources.web_images import CIS, country_code
    cc = country_code(uni)
    local = "ru" if cc in CIS else "en"
    en = uni.names.get("en") or uni.name
    ru = uni.names.get("ru") or uni.name
    site = urlparse(uni.website or "").netloc.lower().removeprefix("www.")
    jobs = []
    for key, _label, qs in TOPICS:
        jobs.append((key, qs[local].replace("{name}", ru if local == "ru" else en), local))
        if local != "en" and key in ("buildings", "dorms", "labs", "reviews"):
            jobs.append((key, qs["en"].replace("{name}", en), "en"))
    results = await asyncio.gather(*[_serper(q, cc, hl) for _, q, hl in jobs])
    sources: list[dict] = []
    seen_urls: set[str] = set()
    pages: list[tuple[str, str, str, list[str]]] = []
    for (key, q, _hl), j in zip(jobs, results):
        box = j.get("answerBox") or {}
        if box.get("snippet") or box.get("answer"):
            sources.append({"title": box.get("title") or q, "url": box.get("link") or "", "kind": "web", "topic": key,
                            "text": box.get("snippet") or box.get("answer")})
        organic = [o for o in j.get("organic") or [] if o.get("link") and not SKIP.search(o["link"])]
        snippets = [f"{o.get('title')}: {o.get('snippet')}" for o in organic[:6] if o.get("snippet")]
        if snippets:
            sources.append({"title": f"Google: «{q}»", "url": f"https://www.google.com/search?q={q.replace(' ', '+')}",
                            "kind": "web", "topic": key, "text": " | ".join(snippets)})
        words = [w for w in q.lower().split() if len(w) > 3 and w not in en.lower() and w not in ru.lower()]
        for o in organic[:2]:
            if o["link"] not in seen_urls:
                seen_urls.add(o["link"])
                pages.append((key, o.get("title") or o["link"], o["link"], words))
    texts = await asyncio.gather(*[_fetch(url, words) for _, _, url, words in pages])
    for (key, title, url, _), text in zip(pages, texts):
        if len(text) > 200:
            dom = urlparse(url).netloc.lower().removeprefix("www.")
            sources.append({"title": title, "url": url, "topic": key, "text": text,
                            "kind": "official" if site and (dom == site or dom.endswith("." + site)) else "web"})
    for title, url, text in await _wiki(uni):
        sources.append({"title": title, "url": url, "kind": "wikipedia", "topic": "all", "text": text})
    rv = await _reviews(uni)
    if rv:
        sources.append({"title": rv[0], "url": rv[1], "kind": "reviews", "topic": "reviews", "text": rv[2]})
    for k, s in enumerate(sources, 1):
        s["id"] = k
    return sources


# ---------- writing ----------
PROMPT = """Ты составляешь для абитуриента честную справку о кампусе вуза «{name}» ({city}).
Ниже пронумерованные источники из интернета: выдачи поиска, страницы сайтов, Википедия, отзывы на Google Картах.
Напиши по-русски, только то, что прямо следует из источников, с номерами источников у каждого утверждения.
Разделы (key: название):
{topics}
Для каждого раздела: 2-6 коротких пунктов - что именно есть (какие корпуса и что в них, какие общежития, сколько
мест, что в комнатах: кровати, шкафы, кухня, душ, стиральные машины, цена; какие библиотеки и залы; какие лаборатории
и оборудование; компьютерные классы, Wi-Fi; спорткомплекс, бассейн; столовые и кафе) и какое это (новое, современное,
старое, тесное, удобное) - если источники это говорят. Если о разделе ничего нет - один пункт «Нет данных в найденных
источниках» без номеров. rating раздела: good / mixed / poor по тому, что пишут студенты и посетители, иначе unknown;
rating_note - почему, одной фразой с номером источника.
summary: 2-3 предложения - какой это кампус в целом.
quick: до 8 коротких фактов-чипов (label: значение), например «Общежитий: 4», «Wi-Fi: по всему кампусу»,
«Библиотека: 24/7», - только подтверждённые источниками.
Ничего не выдумывай, не бери сведения о других вузах, не пересказывай рекламу агентств.

Источники:
{sources}"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "sections": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "key": {"type": "STRING"}, "title": {"type": "STRING"},
            "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "text": {"type": "STRING"}, "sources": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                "required": ["text", "sources"]}},
            "rating": {"type": "STRING", "enum": ["good", "mixed", "poor", "unknown"]},
            "rating_note": {"type": "STRING"}},
            "required": ["key", "title", "items", "rating"]}},
        "quick": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "label": {"type": "STRING"}, "value": {"type": "STRING"},
            "sources": {"type": "ARRAY", "items": {"type": "INTEGER"}}}, "required": ["label", "value"]}},
    },
    "required": ["summary", "sections", "quick"],
}


async def build(uni: University, refresh: bool = False) -> CampusFacts:
    if not refresh:
        hit = await cache.kv_get("facts", uni.qid, max_age_s=TTL)
        if hit:
            return _clean(CampusFacts.model_validate(hit))
    if not settings.serper_api_key:
        return CampusFacts(qid=uni.qid, note="нет ключа SERPER_API_KEY: искать тексты о кампусе негде")
    sources = await gather(uni)
    if not sources:
        return CampusFacts(qid=uni.qid, note="в интернете не нашлось текстов о кампусе")
    listing = "\n\n".join(f"[{s['id']}] {s['title']} ({s['url']})\n{s['text']}" for s in sources)
    topics = "\n".join(f"- {k}: {label}" for k, label, _ in TOPICS)
    prompt = PROMPT.format(name=uni.name, city=uni.city or "", topics=topics, sources=listing)
    out = CampusFacts(qid=uni.qid, sources=[FactSource(id=s["id"], title=s["title"][:140], url=s["url"], kind=s["kind"])
                                            for s in sources],
                      generated_at=datetime.now(timezone.utc).isoformat())
    if settings.active_llm() != "gemini":
        out.note = "нет ключа LLM: справка не составлена"
        return out
    from .ai_inspector import gemini_post
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                 "temperature": 0.2}}
    try:
        j, model = await gemini_post(body, 90.0)
        text = j["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _Out.model_validate_json(text) if text.strip().startswith("{") else None
    except Exception as e:  # noqa: BLE001
        log.warning("campus facts LLM failed for %s: %r", uni.qid, e)
        out.note = f"ИИ не ответил: {type(e).__name__}"
        return out
    if parsed is None:
        out.note = "ИИ вернул не JSON"
        return out
    valid = {s.id for s in out.sources}
    for sec in parsed.sections:
        for it in sec.items:
            it.sources = [k for k in it.sources if k in valid]
    for q in parsed.quick:
        q.sources = [k for k in q.sources if k in valid]
    labels = {k: label for k, label, _ in TOPICS}
    parsed.sections.sort(key=lambda s: list(labels).index(s.key) if s.key in labels else 99)
    for sec in parsed.sections:
        sec.title = labels.get(sec.key, sec.title)
    out.summary, out.sections, out.quick, out.model = parsed.summary, parsed.sections, parsed.quick, f"gemini/{model}"
    _clean(out)
    await cache.kv_set("facts", uni.qid, out.model_dump())
    return out


_REFS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def _clean(f: CampusFacts) -> CampusFacts:
    """The source numbers go in `sources`; the model repeats them inside the text as well."""
    f.summary = _REFS.sub("", f.summary).strip()
    for sec in f.sections:
        sec.rating_note = _REFS.sub("", sec.rating_note).strip()
        for it in sec.items:
            it.text = _REFS.sub("", it.text).strip()
    return f
