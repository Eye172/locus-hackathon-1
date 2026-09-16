"""Official university website crawler: homepage + a few campus-related subpages.

Photos found here belong to the university by definition (the site is the source), so they form the
"brochure" set. Icons, logos, banners and tracking pixels are filtered by URL, and again by size after download.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

from ... import http
from ...models import PhotoCandidate, University

KEYWORDS = [
    (r"campus|kampus|кампус", 5),
    (r"dorm|hostel|zhatak|жатақ|общеж|obshezh|residence", 5),
    (r"gallery|галере|photo|foto|фото|media", 4),
    (r"student-life|studlife|student_life|студенческ|студенттік|life", 3),
    (r"virtual|tour|360|infrastruct|инфраструктур|facilit", 3),
    (r"sport|спорт|library|библиотек|кітапхана|lab|лаборатор", 2),
    (r"about|о-универ|university", 1),
]
KW_RES = [(re.compile(p, re.I), w) for p, w in KEYWORDS]
BAD_URL = re.compile(
    r"logo|icon|sprite|favicon|banner|flag|avatar|placeholder|pixel|button|badge|emblem|\.svg|\.gif|captcha|"
    r"counter|/tr\?|mc\.yandex|google|facebook|vk\.com|instagram|telegram|whatsapp|youtube|qr|thumb_?nail|"
    r"loader|spinner|arrow|bg-|background|pattern|emoji|smile|/flags?/|watermark|partner|sponsor",
    re.I,
)
IMG_URL = re.compile(r"\.(jpe?g|png|webp)(\?|&|$)", re.I)
BINARY_LINK = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|jpe?g|png|gif|webp|mp4|mp3|avi|mov)(\?|$)", re.I)
CSS_URL = re.compile(r"url\((?:'|\")?([^'\")]+\.(?:jpe?g|png|webp)[^'\")]*)", re.I)
YEAR_IN_URL = re.compile(r"/(20\d{2})/(\d{2})/")


def _same_site(base: str, url: str) -> bool:
    a, b = urlparse(base).netloc.lower(), urlparse(url).netloc.lower()
    return a.replace("www.", "") == b.replace("www.", "")


def _link_score(url: str) -> int:
    path = urlparse(url).path + "?" + (urlparse(url).query or "")
    return sum(w for r, w in KW_RES if r.search(path))


async def _fetch_page(url: str, timeout: float) -> tuple[str, str] | None:
    try:
        r = await http.get(url, timeout=timeout)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return None
        return str(r.url), r.text
    except Exception:
        return None


def _extract(final_url: str, html: str) -> tuple[list[dict], list[str], str]:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    images: list[dict] = []
    for m in soup.find_all("meta"):
        if (m.get("property") or m.get("name") or "").lower() in ("og:image", "og:image:secure_url", "twitter:image"):
            if m.get("content"):
                images.append({"src": m["content"], "alt": title, "og": True})
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or img.get("data-original")
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].strip().split(" ")[0]
        if not src or src.startswith("data:"):
            continue
        images.append({"src": src, "alt": (img.get("alt") or img.get("title") or "").strip(), "og": False})
    for src in CSS_URL.findall(html):
        images.append({"src": src, "alt": "", "og": False})
    links = []
    for a in soup.find_all("a", href=True):
        raw = a["href"].strip()
        if raw.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        href = urldefrag(urljoin(final_url, raw))[0]
        if BINARY_LINK.search(href):
            continue
        if href.startswith("http") and _same_site(final_url, href):
            links.append(href)
    return images, links, title


async def collect(uni: University, timeout: float = 6.0, max_subpages: int = 5) -> list[PhotoCandidate]:
    if not uni.website:
        return []
    home = await _fetch_page(uni.website, timeout=min(timeout, 5.0))
    if not home:
        return []
    final_url, html = home
    images, links, title = _extract(final_url, html)
    pages: list[tuple[str, str, list[dict]]] = [(final_url, title, images)]

    scored = {}
    for l in links:
        s = _link_score(l)
        if s >= 2 and l.rstrip("/") != final_url.rstrip("/"):
            scored[l] = max(scored.get(l, 0), s)
    subpages = [l for l, _ in sorted(scored.items(), key=lambda x: -x[1])[:max_subpages]]
    results = await asyncio.gather(*[_fetch_page(l, timeout=4.0) for l in subpages], return_exceptions=True)
    for res in results:
        if isinstance(res, tuple):
            u, h = res
            imgs, _, t = _extract(u, h)
            pages.append((u, t, imgs))

    seen: set[str] = set()
    out: list[PhotoCandidate] = []
    for page_url, page_title, imgs in pages:
        page_score = _link_score(page_url)
        for im in imgs:
            src = urldefrag(urljoin(page_url, im["src"]))[0]
            if not src.startswith("http") or src in seen:
                continue
            if BAD_URL.search(src) or not (IMG_URL.search(src) or im.get("og")):
                continue
            seen.add(src)
            date, date_source = None, None
            m = YEAR_IN_URL.search(src)
            if m:
                date, date_source = f"{m.group(1)}-{m.group(2)}", "url"
            out.append(PhotoCandidate(
                url=src, page_url=page_url, source="official",
                title=im["alt"] or page_title or None,
                text=" ".join(x for x in [im["alt"], page_title, urlparse(src).path, urlparse(page_url).path] if x),
                author=urlparse(final_url).netloc, license="© университет (официальный сайт)",
                date=date, date_source=date_source,
            ))
    # campus/dorm subpage images first, then og:image, then the rest
    def prio(c: PhotoCandidate) -> int:
        return -(_link_score(c.page_url) * 2 + _link_score(c.url))
    out.sort(key=prio)
    return out[:45]
