"""Find a university that is not in Wikidata — "any university on request":

1. OpenStreetMap via Nominatim: universities are mapped almost everywhere (amenity=university / college,
   building=university, office=educational_institution) with a name, coordinates, the city and often the website;
2. Bing (HTML, no key) for the official site when OSM has none; DuckDuckGo blocks automated clients.

The result is a synthetic entity with id "W<hash>", stored in the kv bucket "web"; the profile pipeline treats it like
any Wikidata university (site crawl, geo photos, Mapillary, social channels found on the site).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from ... import cache, http
from ...models import Candidate

log = logging.getLogger("campuslens.websearch")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36")
SKIP_DOMAINS = re.compile(
    r"wikipedia|wikidata|wikimedia|facebook|instagram|vk\.com|youtube|t\.me|telegram|tiktok|linkedin|twitter|x\.com|"
    r"2gis|yandex|google|bing|duckduckgo|apple\.com|play\.google|topuniversities|timeshighereducation|4icu|"
    r"unipage|studyinkazakhstan|study\.kz|edunews|zakon\.kz|tengrinews|inform\.kz|kapital\.kz|nur\.kz|"
    r"ucheba|postupi|vuzoteka|edu\.ru/vuz|univer\.kz|glassdoor|indeed|hh\.kz|hh\.ru|amazon|aliexpress",
    re.I,
)
GENERIC_TITLE = re.compile(r"главная|home|main|official|официальн|welcome|добро пожаловать", re.I)
OSM_KINDS = {("amenity", "university"), ("amenity", "college"), ("building", "university"), ("building", "college"),
             ("office", "educational_institution"), ("amenity", "school")}
GENERIC_WORDS = re.compile(r"\b(university|universitet|университет|университеті|институт|institute|академия|academy|"
                           r"college|колледж|the|of|и|имени|им|атындағы|state|государственный|national|национальный)\b", re.I)


def _norm(s: str) -> str:
    s = re.sub(r"[^\w\s-]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _core(s: str) -> str:
    return re.sub(r"\s+", " ", GENERIC_WORDS.sub(" ", _norm(s))).strip()


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


# ---------------------------------------------------------------- OpenStreetMap (Nominatim)
_nom_lock = asyncio.Lock()
_nom_last = 0.0
_memo: dict[str, tuple[float, dict | None]] = {}


async def _nominatim_rows(q: str) -> list[dict]:
    """Nominatim allows one request per second: serialise and pace calls, retry once on 429."""
    global _nom_last
    async with _nom_lock:
        wait = 1.05 - (time.monotonic() - _nom_last)
        if wait > 0:
            await asyncio.sleep(wait)
        for attempt in range(2):
            try:
                r = await http.get("https://nominatim.openstreetmap.org/search",
                                   params={"q": q, "format": "jsonv2", "limit": 6, "extratags": 1, "addressdetails": 1,
                                           "namedetails": 1, "accept-language": "ru"}, timeout=6.0)
                _nom_last = time.monotonic()
                if r.status_code == 429 and attempt == 0:
                    await asyncio.sleep(1.2)
                    continue
                return r.json() if r.status_code == 200 else []
            except Exception as e:  # noqa: BLE001
                log.info("nominatim failed: %s", e)
                return []
    return []


async def osm_lookup(q: str) -> dict | None:
    """The OSM university whose name matches the query best (fuzzy, generic words stripped); entries with a website win."""
    key = _norm(q)
    hit = _memo.get(key)
    if hit and time.monotonic() - hit[0] < (3600 if hit[1] else 300):  # a miss may be a transient 429: retry sooner
        return hit[1]
    rows = await _nominatim_rows(q)
    if not any((x.get("category"), x.get("type")) in OSM_KINDS for x in rows) and not GENERIC_WORDS.search(q):
        rows = await _nominatim_rows(f"университет {q}")  # "Мирас Шымкент" → "университет Мирас Шымкент"
    qc = _core(q) or _norm(q)
    best: tuple[float, dict] | None = None
    for x in rows:
        if (x.get("category"), x.get("type")) not in OSM_KINDS:
            continue
        names = [x.get("name") or ""] + list((x.get("namedetails") or {}).values())
        score = max((fuzz.WRatio(qc, _core(n) or _norm(n)) for n in names if n), default=0)
        tags0 = x.get("extratags") or {}
        if tags0.get("website") or tags0.get("contact:website"):
            score += 4  # the entry with the official site is the better anchor
        if score >= 72 and (best is None or score > best[0]):
            best = (score, x)
    if not best:
        _memo[key] = (time.monotonic(), None)
        return None
    x = best[1]
    tags = x.get("extratags") or {}
    addr = x.get("address") or {}
    site = tags.get("website") or tags.get("contact:website") or tags.get("url")
    if site and not site.startswith("http"):
        site = "https://" + site
    out = {"name": x.get("name") or (x.get("namedetails") or {}).get("name") or q, "lat": float(x["lat"]), "lon": float(x["lon"]),
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or addr.get("state"),
            "country": addr.get("country"), "website": site, "osm_type": x.get("osm_type"), "osm_id": x.get("osm_id"),
            "match": best[0]}
    _memo[key] = (time.monotonic(), out)
    return out


# ---------------------------------------------------------------- Bing (official site fallback)
def _bing_url(href: str) -> str:
    if "bing.com/ck/a" in href:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            try:
                return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return ""
    return href


async def search(q: str) -> list[dict]:
    try:
        r = await http.get("https://www.bing.com/search", params={"q": q, "setlang": "ru", "count": 10},
                           headers={"User-Agent": BROWSER_UA, "Accept-Language": "ru,en"}, timeout=6.0)
        if r.status_code != 200:
            return []
    except Exception as e:  # noqa: BLE001
        log.info("bing failed: %s", e)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        url = _bing_url(a.get("href", ""))
        if not url.startswith("http"):
            continue
        p = li.select_one(".b_caption p")
        out.append({"title": a.get_text(" ", strip=True), "url": url, "snippet": p.get_text(" ", strip=True) if p else ""})
    return out


def _clean_title(title: str) -> str:
    parts = re.split(r"\s+[|\-–—:]\s+", title)
    parts = [p.strip() for p in parts if p.strip() and not GENERIC_TITLE.fullmatch(p.strip())]
    parts.sort(key=lambda p: -len(re.sub(r"[^\w]", "", p)))
    return (parts[0] if parts else title).strip()[:120]


async def official_site(name: str) -> str | None:
    """Official site of a named university via web search; None unless a result clearly matches the name."""
    core = set(_core(name).split())
    for r in await search(f"{name} официальный сайт"):
        dom = _domain(r["url"])
        if not dom or SKIP_DOMAINS.search(dom) or SKIP_DOMAINS.search(r["url"]):
            continue
        text = set(_norm(f"{r['title']} {r['snippet']} {dom}").split())
        if len(core & text) >= max(1, min(2, len(core))):
            return f"{urlparse(r['url']).scheme}://{urlparse(r['url']).netloc}/"
    return None


# ---------------------------------------------------------------- entry point
async def find_university(q: str, city_hint: str | None = None) -> Candidate | None:
    osm = await osm_lookup(q if not city_hint or city_hint in _norm(q) else f"{q} {city_hint}")
    if not osm:
        return None
    key = f"osm:{osm['osm_type']}:{osm['osm_id']}"
    qid = "W" + hashlib.sha1(key.encode()).hexdigest()[:12]
    ent = await cache.kv_get("web", qid)
    if not ent:
        site = osm.get("website") or await official_site(osm["name"])
        ent = {"qid": qid, "name": osm["name"], "website": site, "domain": _domain(site) if site else None,
               "lat": osm["lat"], "lon": osm["lon"], "coord_source": "osm", "city": osm.get("city") or city_hint,
               "country": osm.get("country"), "osm_type": osm.get("osm_type"), "osm_id": osm.get("osm_id"),
               "snippet": None, "query": q}
        await cache.kv_set("web", qid, ent)
    where = " · ".join(x for x in (ent.get("city"), ent.get("domain") or "сайт не найден") if x)
    return Candidate(qid=qid, label=ent["name"], description=f"найден на карте OSM · {where}", city=ent.get("city"),
                     country=ent.get("country"), score=0.92, origin="web")  # above fuzzy index noise, below exact hits
