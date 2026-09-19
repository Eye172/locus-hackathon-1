"""What a student wants to know about a university, read from its own website and its Wikipedia article:
sports, food, housing, student life, programmes, admission and costs, international students, the campus.

crawl() finds the relevant pages of the official site - links on the home page and the sitemap, matched against
topic words in several languages - and reads their text; wiki_sections() takes the matching sections of the
Wikipedia article. One LLM call turns both into short facts per topic, each with its sources. The digest is cached
for 14 days and feeds the compare page's advisor (compare.advisor_sheet)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import cache, http
from ..config import settings
from ..models import University

log = logging.getLogger("campuslens.site_digest")

VERSION = 1
PAGE_CHARS = 3500
MAX_PAGES = 16
TOPICS: dict[str, tuple[str, list[str]]] = {
    "sports": ("Спорт и активности", [
        "sport", "athletic", "gym", "fitness", "pool", "stadium", "recreation", "спорт", "фитнес", "бассейн", "стадион",
        "физкульт", "spor", "wychowania fizycznego", "azs", "hochschulsport", "体育", "スポーツ", "체육"]),
    "food": ("Еда и столовые", [
        "dining", "food", "cafeteria", "canteen", "cafe", "restaurant", "meal", "столов", "питани", "кафе", "буфет",
        "yemek", "kafeterya", "stołówk", "gastronom", "mensa", "食堂", "餐饮", "学食", "식당"]),
    "housing": ("Общежития и жильё", [
        "housing", "dorm", "residence", "accommodation", "hostel", "общежит", "жильё", "жилье", "проживан",
        "студгородок", "yurt", "barınma", "akademik", "dom studenta", "domy studenckie", "wohnheim", "wohnen",
        "宿舍", "学生寮", "기숙사", "жатақхана"]),
    "student_life": ("Студенческая жизнь, клубы, мероприятия", [
        "student life", "campus life", "student-life", "club", "societ", "activit", "event", "organization",
        "студенческ", "клуб", "внеучеб", "досуг", "творчеств", "öğrenci", "kulüp", "topluluk", "samorząd",
        "koła naukowe", "studentenleben", "学生活动", "社团", "サークル", "동아리", "студенттік"]),
    "academics": ("Факультеты, программы, язык обучения", [
        "facult", "school of", "programs", "programmes", "undergraduate", "graduate", "department", "academic",
        "факультет", "институт", "программ", "специальност", "направлени", "образован", "fakülte", "bölüm",
        "wydział", "kierunk", "studiengang", "院系", "学部", "학과"]),
    "admission_costs": ("Поступление, стоимость, стипендии", [
        "admission", "apply", "tuition", "fee", "scholarship", "financial aid", "grant", "cost", "поступ",
        "абитуриент", "приём", "прием", "стоимост", "оплат", "стипенди", "грант", "kabul", "aday", "burs", "ücret",
        "rekrutacj", "opłat", "stypendi", "bewerbung", "studiengebühr", "招生", "学费", "入試", "입학", "түлек", "талапкер"]),
    "international": ("Иностранным студентам и обмены", [
        "international", "exchange", "erasmus", "foreign", "study abroad", "международ", "иностран", "обмен",
        "uluslararası", "yabancı", "międzynarod", "cudzoziem", "internationale", "国际", "留学", "국제", "халықаралық"]),
    "campus": ("Кампус: библиотека, лаборатории, инфраструктура", [
        "campus", "library", "facilit", "infrastructure", "labs", "кампус", "библиотек", "инфраструктур",
        "лаборатор", "kampüs", "kütüphane", "biblioteka", "bibliothek", "图书馆", "캠퍼스"]),
    "rankings": ("Рейтинги и репутация", ["ranking", "about", "history", "рейтинг", "о университете", "об университете",
                                          "история", "hakkında", "o uczelni", "über uns", "简介", "概要"]),
}
WIKI_HEADINGS = ["campus", "student life", "athletic", "sport", "housing", "residen", "dining", "academic", "ranking",
                 "reputation", "admission", "tuition", "organi", "faculties", "schools", "student body", "traditions",
                 "library", "research", "кампус", "студенческ", "спорт", "общежит", "рейтинг", "поступ", "факультет",
                 "структур", "инфраструктур", "образован", "деятельност"]
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|docx?|xlsx?|pptx?|zip|rar|mp4|mp3)(\?|$)", re.I)
TWO_PART_TLD = {"edu", "ac", "com", "org", "gov", "net", "co"}

_running: dict[str, asyncio.Task] = {}
_REFS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")   # the model repeats the source numbers inside the text


def _base_domain(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in TWO_PART_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_LETTER = "a-zа-яёçğıöşüąćęłńóśźżäößéèàùâêîôûқғңұүһәөі"


def _has(word: str, text: str) -> bool:
    """`word` at the start of a word ('sports', 'спортивный'), not inside one ('transportation')."""
    return re.search(rf"(?<![{_LETTER}]){re.escape(word)}", text) is not None


def _topic_hits(text: str) -> dict[str, int]:
    t = text.lower()
    hits = {k: sum(1 for w in words if _has(w, t)) for k, (_, words) in TOPICS.items()}
    return {k: v for k, v in hits.items() if v}


def _links(html: str, base_url: str, domain: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        u = urlparse(url)
        if u.scheme not in ("http", "https") or SKIP_EXT.search(u.path) or not _base_domain(u.netloc) == domain:
            continue
        out.append((url, " ".join(a.get_text(" ").split())[:120]))
    return out


def _text(html: str, words: list[str]) -> tuple[str, str]:
    """(title, main text) of a page: paragraphs, list items and table cells; the ones about the topic first."""
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join((soup.title.get_text(" ") if soup.title else "").split())[:140]
    for t in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "svg", "aside", "iframe"]):
        t.decompose()
    blocks, seen = [], set()
    for x in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "td", "dd"]):
        b = " ".join(x.get_text(" ").split())
        if len(b) > 30 and b not in seen:
            seen.add(b)
            blocks.append(b)
    hit = [b for b in blocks if any(w in b.lower() for w in words)]
    return title, " ".join(hit + [b for b in blocks if b not in hit])[:PAGE_CHARS]


async def _get(url: str, timeout: float = 8.0):
    try:
        r = await http.get(url, timeout=timeout)
        return r if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


async def _sitemap_urls(root: str, domain: str) -> list[str]:
    maps = []
    robots = await _get(urljoin(root, "/robots.txt"), 5.0)
    if robots is not None:
        maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots.text)[:3]
    maps = maps or [urljoin(root, "/sitemap.xml")]
    urls: list[str] = []
    for m in maps:
        r = await _get(m, 8.0)
        if r is None:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)[:4000]
        if "<sitemapindex" in r.text[:500]:
            for child in locs[:4]:
                rc = await _get(child, 8.0)
                if rc is not None:
                    urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rc.text)[:3000]
        else:
            urls += locs
    return [u for u in urls if _base_domain(urlparse(u).netloc) == domain and not SKIP_EXT.search(u)]


async def crawl(website: str | None) -> list[dict]:
    """[{url, title, text, topics}] - the home page and up to MAX_PAGES topic pages of the official site."""
    if not website:
        return []
    root = website if website.startswith("http") else f"https://{website}"
    home = await _get(root, 10.0)
    if home is None:
        return []
    final = str(home.url)
    domain = _base_domain(urlparse(final).netloc)
    if "html" not in home.headers.get("content-type", "html"):
        return []
    links = await asyncio.to_thread(_links, home.text, final, domain)
    scored: dict[str, dict[str, int]] = {}
    for url, anchor in links:
        hits = _topic_hits(f"{anchor} {urlparse(url).path.replace('-', ' ').replace('_', ' ')}")
        if hits:
            cur = scored.setdefault(url, {})
            for k, v in hits.items():
                cur[k] = max(cur.get(k, 0), v + (1 if anchor else 0))
    if len(scored) < 8:   # a thin or script-built home page: the sitemap lists the pages instead
        for url in await _sitemap_urls(final, domain):
            hits = _topic_hits(urlparse(url).path.replace("-", " ").replace("_", " "))
            if hits:
                scored.setdefault(url, hits)
    def pick(pool: dict[str, dict[str, int]], topics, taken: list[str]) -> list[str]:
        out: list[str] = []
        for k in topics:   # the best two pages per topic (shorter paths first: section pages, not news items)
            ranked = sorted((u for u, h in pool.items() if k in h), key=lambda u: (-pool[u][k], len(urlparse(u).path)))
            n = 0
            for u in ranked:
                if u not in taken and u not in out:
                    out.append(u)
                    n += 1
                    if n >= 2:
                        break
        return out

    sem = asyncio.Semaphore(6)
    htmls: dict[str, str] = {}

    async def read(url: str, html: str | None = None) -> dict | None:
        async with sem:
            if html is None:
                r = await _get(url)
                if r is None or "html" not in r.headers.get("content-type", ""):
                    return None
                html = r.text
        htmls[url] = html
        topics = sorted(scored.get(url, {}), key=lambda k: -scored[url][k]) if url in scored else ["about"]
        words = [w for k in topics if k in TOPICS for w in TOPICS[k][1]]
        title, text = await asyncio.to_thread(_text, html, words)
        return {"url": url, "title": title, "text": text, "topics": topics} if len(text) >= 250 else None

    picked = pick(scored, TOPICS, [])[:MAX_PAGES]
    pages = [p for p in await asyncio.gather(read(final, home.text), *(read(u) for u in picked)) if p]
    covered = {k for p in pages for k in p["topics"]}
    missing = [k for k in TOPICS if k not in covered]
    if missing and len(pages) < MAX_PAGES + 1:
        hop: dict[str, dict[str, int]] = {}
        for url, html in htmls.items():
            if url == final:
                continue
            for link, anchor in await asyncio.to_thread(_links, html, url, domain):
                hits = {k: v for k, v in _topic_hits(f"{anchor} {urlparse(link).path.replace('-', ' ').replace('_', ' ')}").items() if k in missing}
                if hits and link not in htmls:
                    cur = hop.setdefault(link, {})
                    for k, v in hits.items():
                        cur[k] = max(cur.get(k, 0), v + (1 if anchor else 0))
        scored.update({u: h for u, h in hop.items() if u not in scored})
        more = pick(hop, missing, list(htmls))[:MAX_PAGES + 1 - len(pages)]
        pages += [p for p in await asyncio.gather(*(read(u) for u in more)) if p]
    return pages


async def wiki_sections(uni: University, max_chars: int = 9000) -> list[dict]:
    """The student-relevant sections of the Wikipedia article (English first, it is usually the fullest)."""
    out, total = [], 0
    langs = [lg for lg in dict.fromkeys(["en", "ru", *(uni.wikipedia or {})]) if (uni.wikipedia or {}).get(lg)]
    for lg in langs[:2]:
        title = uni.wikipedia[lg]
        try:
            j = await http.get_json(f"https://{lg}.wikipedia.org/w/api.php", params={
                "action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki", "redirects": 1,
                "titles": title, "format": "json"}, timeout=8.0)
        except Exception as e:  # noqa: BLE001
            log.info("wiki %s:%s failed: %r", lg, title, e)
            continue
        pages = (j.get("query") or {}).get("pages") or {}
        text = (next(iter(pages.values()), {}).get("extract") or "") if pages else ""
        url = f"https://{lg}.wikipedia.org/wiki/{title.replace(' ', '_')}"
        parts = re.split(r"\n(={2,3})\s*(.+?)\s*\1\n", "\n" + text)
        lead = parts[0].strip()
        if lead:
            chunk = lead[:1800]
            out.append({"url": url, "title": f"Википедия ({lg}): {title}", "text": chunk})
            total += len(chunk)
        for i in range(1, len(parts) - 2, 3):
            head, body = parts[i + 1], parts[i + 2].strip()
            if body and any(_has(w, head.lower()) for w in WIKI_HEADINGS):
                chunk = f"{head}: {body}"[:2500]
                out.append({"url": f"{url}#{head.replace(' ', '_')}", "title": f"Википедия ({lg}): {head}", "text": chunk})
                total += len(chunk)
            if total >= max_chars:
                return out
    return out


PROMPT = """Ниже — тексты страниц официального сайта университета «{name}» ({city}) и разделы статьи Википедии о нём, каждый под номером [n].
Составь для абитуриента справку по темам. Правила:
- Бери только то, что прямо написано в текстах. Числа, цены с валютой, названия, адреса и имена сохраняй точно. Ничего не додумывай.
- Каждый факт — одно-два коротких предложения на русском, конкретно и полезно для выбора вуза (что есть, сколько стоит, как устроено, где находится), с номерами источников.
- Не пересказывай новости, приказы, контакты и навигацию сайта. Не повторяй один факт в разных темах.
- Если по теме в текстах ничего нет, пропусти её. До 8 фактов на тему.
Темы (key — описание):
{topics}
В summary — 2–3 предложения: что это за вуз и чем он выделяется по этим текстам.

Тексты:
{sources}"""
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "topics": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "key": {"type": "STRING", "enum": list(TOPICS)},
            "facts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "text": {"type": "STRING"}, "sources": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                "required": ["text", "sources"]}}},
            "required": ["key", "facts"]}},
    },
    "required": ["summary", "topics"],
}


async def _build(uni: University) -> dict:
    pages, wiki = await asyncio.gather(crawl(uni.website), wiki_sections(uni))
    sources = [{"id": i + 1, "title": s["title"] or s["url"], "url": s["url"], "kind": kind, "text": s["text"]}
               for i, (kind, s) in enumerate([("official", p) for p in pages] + [("wikipedia", w) for w in wiki])]
    out = {"v": VERSION, "qid": uni.qid, "summary": "", "topics": {}, "generated_at": datetime.now(timezone.utc).isoformat(),
           "pages": len(pages), "wiki_sections": len(wiki),
           "sources": [{k: s[k] for k in ("id", "title", "url", "kind")} for s in sources]}
    if not sources or settings.active_llm() != "gemini":
        return out
    from .ai_inspector import gemini_post
    listing = "\n\n".join(f"[{s['id']}] {s['title']} ({s['url']})\n{s['text']}" for s in sources)
    prompt = PROMPT.format(name=uni.name, city=uni.city or "", sources=listing,
                           topics="\n".join(f"- {k} — {label}" for k, (label, _) in TOPICS.items()))
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2}}
    j, model = await gemini_post(body, 60.0)
    parsed = json.loads("".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought")))
    urls = {s["id"]: s["url"] for s in sources}
    for t in parsed.get("topics", []):
        facts = [{"text": _REFS.sub("", f["text"]).strip(), "sources": [urls[i] for i in f.get("sources", []) if i in urls]}
                 for f in t.get("facts", []) if f.get("text", "").strip()]
        if facts and t.get("key") in TOPICS:
            out["topics"].setdefault(t["key"], []).extend(facts[:8])
    out["summary"], out["model"] = parsed.get("summary", "").strip(), model
    return out


async def digest(uni: University, wait: float | None = None) -> dict | None:
    """The cached digest, or a new one (one build per university at a time; it goes on after `wait` runs out)."""
    key = f"v{VERSION}:{uni.qid}"
    hit = await cache.kv_get("site_digest", key, max_age_s=14 * 86400)
    if hit:
        return hit
    task = _running.get(uni.qid)
    if task is None or task.done():
        async def run():
            try:
                d = await _build(uni)
            except Exception as e:  # noqa: BLE001
                log.warning("site digest %s failed: %r", uni.qid, e)
                return None
            if d.get("topics"):
                await cache.kv_set("site_digest", key, d)
            log.info("site digest %s: %d site pages, %d wiki sections -> %s", uni.qid, d["pages"], d["wiki_sections"],
                     {k: len(v) for k, v in d["topics"].items()})
            return d
        task = _running[uni.qid] = asyncio.ensure_future(run())
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=wait) if wait else await task
    except asyncio.TimeoutError:
        return None
