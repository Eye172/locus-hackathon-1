"""Image search beyond the university's own channels.

- Google Images through Serper (key SERPER_API_KEY): one query per category ("<university> общежитие", …) in the
  language of the country, so dorms, libraries and gyms that no wiki knows about still show up. Search results are
  only candidates: the AI inspector decides, and every photo keeps a link to the page it was found on.
- Wikimedia Commons full-text search: files that mention the university but sit outside its category.
- Openverse: openly licensed photos (mostly Flickr) with author and licence; no key, ~200 requests a day.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ... import http
from ...config import settings
from ...models import PhotoCandidate, University
from . import commons
from .official_site import _page_date

log = logging.getLogger("campuslens.web_images")

CIS = {"KZ", "KG", "UZ", "TJ", "TM", "RU", "BY", "AZ", "AM", "MD"}
QUERY_WORDS = {
    "ru": [("campus", "кампус"), ("dormitory", "общежитие"), ("classroom", "аудитория"), ("library", "библиотека"),
           ("lab", "лаборатория"), ("sports", "спортзал"), ("student_life", "студенты")],
    "en": [("campus", "campus"), ("dormitory", "dormitory"), ("classroom", "lecture hall"), ("library", "library"),
           ("lab", "laboratory"), ("sports", "sports hall"), ("student_life", "students")],
}
SKIP_DOMAINS = re.compile(
    r"shutterstock|istockphoto|gettyimages|alamy|depositphotos|dreamstime|123rf|freepik|vecteezy|pinterest|"
    r"pinimg|lookaside\.|fbsbx|cdninstagram|tiktok|wikimedia|wikipedia|youtube|ytimg|pngtree|clipart|stock|"
    r"adobe\.com|canva\.com|dribbble|behance|vk\.com|userapi\.com|ok\.ru|twimg|x\.com",
    re.I,
)
COUNTRIES_JSON = Path(__file__).resolve().parents[4] / "frontend" / "public" / "countries.json"


@lru_cache(maxsize=1)
def _iso_by_qid() -> dict[str, str]:
    try:
        return {c["qid"]: c["iso"] for c in json.loads(COUNTRIES_JSON.read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return {"Q232": "KZ", "Q813": "KG", "Q265": "UZ", "Q863": "TJ", "Q874": "TM", "Q159": "RU"}


def country_code(uni: University) -> str | None:
    from ..resolve import index  # late import: resolve loads the whole index
    row = index.by_id.get(uni.qid)
    return _iso_by_qid().get(row["country"]) if row and row.get("country") else None


def _domain(url: str | None) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def _short_name(uni: University, lang: str) -> str:
    ru, en = uni.names.get("ru") or uni.name, uni.names.get("en")
    if lang == "en":
        return en or ru
    return ru if len(ru) <= 60 or not en else en


# ---------- Google Images (Serper) ----------
_serper_sem = asyncio.Semaphore(4)   # the account allows 5 requests a second; a burst of ten gets some refused


async def _serper(q: str, gl: str | None, hl: str, num: int = 10) -> list[dict]:
    body = {"q": q, "hl": {"nb": "no"}.get(hl, hl), "num": num}
    if gl:
        body["gl"] = gl.lower()
    async with _serper_sem:
        r = await http.post("https://google.serper.dev/images", json=body,
                            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                            timeout=6.0)
    r.raise_for_status()
    return r.json().get("images", [])


async def google_images(uni: University, deep: bool = False) -> list[PhotoCandidate]:
    """One query per place an applicant wants to see. The fast profile asks the seven core categories in the main
    language (Russian in the CIS, English elsewhere). The deep pass, while the profile is already on screen, adds
    assembly halls and canteens and repeats the search in the university's own language, where its students and the
    local press write ("東京大学 学生寮", "Technische Universität München Wohnheim")."""
    if not settings.serper_api_key:
        return []
    from ..langs import CONCEPTS, local_lang
    cc = country_code(uni)
    lang = "ru" if cc in CIS else "en"
    name = _short_name(uni, lang)
    if not deep:
        queries = [(cat, f"{name} {word}", lang) for cat, word in QUERY_WORDS[lang]]
        if lang == "ru" and uni.names.get("en"):
            queries.append(("campus", f"{uni.names['en']} campus", "en"))
    else:
        queries = [(c, f"{name} {CONCEPTS[lang][c]}", lang) for c in ("assembly_hall", "canteen")]
        local = local_lang(uni)
        native = uni.names.get(local)
        if local not in (lang, "en") and local in CONCEPTS and native:
            queries += [(c, f"{native} {CONCEPTS[local][c]}", local)
                        for c in ("campus", "dormitory", "classroom", "assembly_hall", "student_life")]
    results = await asyncio.gather(*[_serper(q, cc, hl) for _, q, hl in queries], return_exceptions=True)
    site = _domain(uni.website)
    seen: set[str] = set()
    out: list[PhotoCandidate] = []
    for (cat, q, _hl), res in zip(queries, results):
        if isinstance(res, Exception):
            log.warning("serper %r failed: %r", q, res)
            continue
        for im in res:
            url, page = im.get("imageUrl"), im.get("link")
            dom = _domain(page)
            if not url or not page or url in seen or not url.startswith("http"):
                continue
            if SKIP_DOMAINS.search(dom) or SKIP_DOMAINS.search(_domain(url)):
                continue
            w, h = im.get("imageWidth") or 0, im.get("imageHeight") or 0
            if w and h and min(w, h) < 360:
                continue
            seen.add(url)
            official = bool(site) and (dom == site or dom.endswith("." + site))
            out.append(PhotoCandidate(
                url=url, page_url=page, source="official" if official else "web_image",
                title=(im.get("title") or "").strip() or None,
                text=" ".join(x for x in [im.get("title"), dom, word_of(q, name)] if x),
                author=im.get("source") or dom,
                license="© университет (официальный сайт)" if official else "© правообладатель; показано превью со ссылкой на источник",
                width=w or None, height=h or None, collector=f"google:{q}",
            ))
    await _page_dates(out)
    return out


def word_of(q: str, name: str) -> str:
    return q.replace(name, "").strip()


async def _page_dates(cands: list[PhotoCandidate], limit: int = 30) -> None:
    """Publication date of the page each image was found on (meta article:published_time, <time datetime>)."""
    pages: dict[str, list[PhotoCandidate]] = {}
    for c in cands:
        if not c.date:
            pages.setdefault(c.page_url, []).append(c)
    sem = asyncio.Semaphore(10)

    async def one(url: str) -> None:
        async with sem:
            try:
                r = await http.get(url, timeout=3.0)
                if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                    return
                d = _page_date(BeautifulSoup(r.text[:300_000], "lxml"))
            except Exception:  # noqa: BLE001
                return
        if d:
            for c in pages[url]:
                c.date, c.date_source = d, "page"

    # dates are a bonus: whatever answers within 2.5 s is used, the rest stays undated
    tasks = [asyncio.create_task(one(u)) for u in list(pages)[:limit]]
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=2.5)
        for t in pending:
            t.cancel()


# ---------- Commons full-text search ----------
async def commons_search(uni: University, limit: int = 30) -> list[PhotoCandidate]:
    names = list(dict.fromkeys(n for n in [uni.names.get("en"), uni.names.get("ru") or uni.name] if n and len(n) >= 6))
    titles: list[str] = []
    for n in names:
        try:
            d = await http.get_json(commons.API, params={
                "action": "query", "list": "search", "srsearch": f'"{n}"', "srnamespace": 6,
                "srlimit": limit, "format": "json"}, timeout=5.0)
        except Exception:  # noqa: BLE001
            continue
        # campus photos are JPEGs; text search also returns PNG charts and micrographs uploaded by the university
        titles += [m["title"] for m in d.get("query", {}).get("search", []) if re.search(r"\.jpe?g$", m["title"], re.I)]
    titles = list(dict.fromkeys(titles))[:40]
    if not titles:
        return []
    infos = await commons.image_info(titles)
    return commons.to_candidates(infos, "commons_search")


# ---------- Openverse ----------
async def openverse(uni: University, limit: int = 20) -> list[PhotoCandidate]:
    q = uni.names.get("en") or uni.name
    try:
        d = await http.get_json("https://api.openverse.org/v1/images/", params={
            "q": q, "page_size": limit, "excluded_source": "wikimedia", "mature": "false"}, timeout=5.0)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in d.get("results", []):
        if not x.get("url") or not x.get("foreign_landing_url"):
            continue
        if (x.get("width") or 1000) < 360 or (x.get("height") or 1000) < 360:
            continue
        lic = f"CC {x['license'].upper()} {x.get('license_version') or ''}".strip() if x.get("license") else None
        out.append(PhotoCandidate(
            url=x["url"], page_url=x["foreign_landing_url"], source="openverse",
            title=x.get("title"), text=" ".join([x.get("title") or "", " ".join(t.get("name", "") for t in x.get("tags") or [])]),
            author=x.get("creator"), license=lic, width=x.get("width"), height=x.get("height"),
            collector=f"openverse:{x.get('provider')}",
        ))
    return out
