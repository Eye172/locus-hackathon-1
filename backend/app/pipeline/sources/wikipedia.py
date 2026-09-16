"""Wikipedia REST summary (description, coordinates fallback) and article image lists."""
from __future__ import annotations

import re
from urllib.parse import quote

from ... import http

PHOTO_EXT = re.compile(r"\.(jpe?g|png|webp)$", re.I)


async def summary(lang: str, title: str) -> dict | None:
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}"
    try:
        r = await http.get(url, timeout=4.0)
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "lang": lang,
            "title": d.get("title"),
            "extract": d.get("extract"),
            "description": d.get("description"),
            "lat": (d.get("coordinates") or {}).get("lat"),
            "lon": (d.get("coordinates") or {}).get("lon"),
            "thumbnail": (d.get("thumbnail") or {}).get("source"),
            "url": (d.get("content_urls") or {}).get("desktop", {}).get("page"),
        }
    except Exception:
        return None


async def best_summary(sitelinks: dict[str, str]) -> dict | None:
    for lang in ("ru", "en", "kk"):
        if lang in sitelinks:
            s = await summary(lang, sitelinks[lang])
            if s and s.get("extract"):
                return s
    return None


async def article_images(lang: str, title: str, limit: int = 40) -> list[str]:
    """Return Commons-style 'File:...' titles of photos used in the article."""
    try:
        d = await http.get_json(f"https://{lang}.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "images", "titles": title, "imlimit": limit, "format": "json",
        }, timeout=4.0)
    except Exception:
        return []
    out: list[str] = []
    for page in d.get("query", {}).get("pages", {}).values():
        for im in page.get("images", []):
            t = im.get("title", "")
            # local namespace name (Файл:, Сурет:) -> File:
            name = t.split(":", 1)[1] if ":" in t else t
            if PHOTO_EXT.search(name):
                out.append("File:" + name)
    return out
