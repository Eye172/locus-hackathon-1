"""Find a university that is not in Wikidata — "any university on request":

1. OpenStreetMap via Nominatim: universities are mapped almost everywhere (amenity=university / college,
   building=university, office=educational_institution) with a name, coordinates, the city and often the website;
2. Google Maps through serper.dev when OSM has nothing. Branch campuses live here and nowhere else: "Cardiff
   University Kazakhstan" in Astana is absent from Wikidata and from OpenStreetMap, but Google has it with
   coordinates and a website, because its students review it;
3. Bing (HTML, no key) for the official site when neither has one; DuckDuckGo blocks automated clients.

The result is a synthetic entity with id "W<hash>", stored in the kv bucket "web"; the profile pipeline treats it like
any Wikidata university (site crawl, geo photos, Mapillary, social channels found on the site).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from functools import lru_cache
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from ... import cache, http
from ...config import settings
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
           "name_en": (x.get("namedetails") or {}).get("name:en"),
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


def _place_words() -> set[str]:
    """City and country words, in every spelling the index knows. They are where, not who."""
    from ..resolve import CITY_ALIASES, country_tokens, normalize, translit
    out = set(country_tokens())
    for canon, aliases in CITY_ALIASES.items():
        for n in [canon, *aliases]:
            out |= {normalize(n), translit(normalize(n))}
    return {w for w in out if len(w) >= 4}


def _name_core(text: str) -> str:
    """The name without the generic words and without the place: "Cardiff University Kazakhstan" -> "cardiff".

    Matching a maps result against the whole query is how "Кардиффский университет Астана" ends up as Astana IT
    University: the city word is in both strings and it carries most of the similarity."""
    words = [w for w in (_core(text) or _norm(text)).split() if w not in _place_words()]
    return " ".join(words) or (_core(text) or _norm(text))


# ---------------------------------------------------------------- Google Maps (serper.dev)
async def gmaps_lookup(q: str) -> dict | None:
    """The Google Maps place whose name matches the query. Cached per query: a branch campus is looked up once.

    Google is asked only after OpenStreetMap has failed, and the answer is accepted only if the name really matches -
    a maps query always returns *something* nearby, and "the nearest university to your words" is not an answer."""
    if not settings.serper_api_key:
        return None
    key = f"gmaps:{_norm(q)}"
    hit = await cache.kv_get("web_place", key, max_age_s=30 * 86400)
    if hit is not None:
        return hit or None
    try:
        r = await http.post("https://google.serper.dev/maps", json={"q": q, "hl": "ru"},
                            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                            timeout=8.0)
        r.raise_for_status()
        places = r.json().get("places") or []
    except Exception as e:  # noqa: BLE001
        log.info("serper maps failed: %s", e)
        return None
    qc = _name_core(q)
    best: dict | None = None
    for p in places[:5]:
        title, lat, lon = p.get("title") or "", p.get("latitude"), p.get("longitude")
        if lat is None or lon is None:
            continue
        tc = _name_core(title)
        # the query may be Cyrillic and the place latin ("Кардиффский университет" / "Cardiff University Kazakhstan")
        from ..resolve import translit
        score = max(fuzz.WRatio(qc, tc), fuzz.WRatio(translit(qc), tc))
        if score < 62 or (best and score <= best["match"]):
            continue
        site = p.get("website") or None
        addr = p.get("address") or ""
        best = {"name": title, "lat": float(lat), "lon": float(lon), "website": site,
                # "49GX+RG7, Астана 020000, Казахстан" -> city "Астана", the postcode is not part of the name
                "city": (re.sub(r"[\d]+", "", addr.split(",")[-2]).strip() or None) if addr.count(",") >= 2 else None,
                "country": (addr.split(",")[-1].strip() or None) if "," in addr else None,
                "place_id": p.get("placeId"), "address": addr, "match": score}
    await cache.kv_set("web_place", key, best or {})
    return best


async def serper_site(name: str, city: str | None = None) -> str | None:
    """Official site through Google (serper.dev), cached. Bing is the no-key fallback in `official_site`."""
    if not settings.serper_api_key:
        return None
    q = " ".join(x for x in (name, city, "официальный сайт") if x)
    key = f"site:{_norm(q)}"
    hit = await cache.kv_get("web_place", key, max_age_s=30 * 86400)
    if hit is not None:
        return hit.get("url") or None
    try:
        r = await http.post("https://google.serper.dev/search", json={"q": q, "hl": "ru", "num": 6},
                            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                            timeout=8.0)
        r.raise_for_status()
        rows = r.json().get("organic") or []
    except Exception as e:  # noqa: BLE001
        log.info("serper search failed: %s", e)
        return None
    core = set(_name_core(name).split())
    for row in rows:
        url, dom = row.get("link") or "", _domain(row.get("link") or "")
        if not dom or SKIP_DOMAINS.search(dom) or SKIP_DOMAINS.search(url):
            continue
        text = set(_norm(f"{row.get('title', '')} {row.get('snippet', '')} {dom}").split())
        if core & text:
            site = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
            await cache.kv_set("web_place", key, {"url": site})
            return site
    await cache.kv_set("web_place", key, {})
    return None


@lru_cache(maxsize=1)
def _iso_by_country() -> dict[str, str]:
    from ..resolve import COUNTRIES_JSON, normalize
    try:
        rows = json.loads(COUNTRIES_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {normalize(row[k]): row["iso"].lower() for row in rows for k in ("ru", "en", "kk") if row.get(k)}


async def _local_site(name: str, city: str | None, country: str | None, site: str | None) -> str | None:
    """A branch campus is often listed with its parent's address: Google gives cardiff.ac.uk/kazakhstan for the
    campus in Astana, whose own site is cardiff.edu.kz. Crawling the parent would fill the profile with photos of
    another country, so when the place sits in a country with its own domain zone, that zone wins."""
    iso = _iso_by_country().get(_norm(country or ""))
    if not iso or (site and _domain(site).endswith("." + iso)):
        return site
    found = await serper_site(name, city) or await official_site(f"{name} {city or ''}".strip())
    return found if found and _domain(found).endswith("." + iso) else site


# ---------------------------------------------------------------- entry point
def _same_city(city: str | None, hint: str | None) -> bool:
    """Is this the city the query asked for? Hints arrive normalised ("astana"), cities as written ("Астана")."""
    if not hint or not city:
        return False
    from ..resolve import CITY_ALIASES, normalize, translit
    names = {city} | {k for k, v in CITY_ALIASES.items() if city in v} | set(CITY_ALIASES.get(city, []))
    forms = {f for n in names for f in (normalize(n), translit(normalize(n)))}
    return hint in forms


async def find_university(q: str, city_hint: str | None = None) -> Candidate | None:
    asked = q if not city_hint or city_hint in _norm(q) else f"{q} {city_hint}"
    osm = await osm_lookup(asked)
    place = osm or await gmaps_lookup(asked)
    if not place:
        return None
    on_map = "OSM" if osm else "Google Картах"
    key = f"osm:{place['osm_type']}:{place['osm_id']}" if osm else f"gmaps:{place.get('place_id') or _norm(q)}"
    qid = "W" + hashlib.sha1(key.encode()).hexdigest()[:12]
    ent = await cache.kv_get("web", qid)
    if not ent:
        site = await _local_site(place["name"], place.get("city"), place.get("country"),
                                 place.get("website")) or await official_site(place["name"])
        ent = {"qid": qid, "name": place["name"], "website": site, "domain": _domain(site) if site else None,
               "lat": place["lat"], "lon": place["lon"], "coord_source": "osm" if osm else "gmaps",
               "city": place.get("city") or city_hint, "country": place.get("country"),
               "osm_type": place.get("osm_type"), "osm_id": place.get("osm_id"), "snippet": None, "query": q}
        await cache.kv_set("web", qid, ent)
    if "name_en" not in ent:  # also for places saved before names were translated
        from ..names import english
        ent["name_en"] = await english(place.get("name_en") or ent["name"], (), ent.get("country"), timeout=3.0)
        await cache.kv_set("web", qid, ent)
    where = " · ".join(x for x in (ent.get("city"), ent.get("domain") or "сайт не найден") if x)
    # a place that really stands in the city the query named beats a fuzzy name match in another country
    score = 0.99 if _same_city(ent.get("city"), city_hint) else 0.92
    from ..names import web_display
    return Candidate(qid=qid, label=web_display(ent["name"], ent.get("name_en"), ent.get("country")), description=f"найден на {on_map} · {where}", city=ent.get("city"),
                     country=ent.get("country"), score=score, origin="web")
