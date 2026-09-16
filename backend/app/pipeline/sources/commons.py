"""Wikimedia Commons: category members, structured-data 'depicts', geosearch, and file metadata."""
from __future__ import annotations

import html
import re
from urllib.parse import quote

from ... import http
from ...models import PhotoCandidate

API = "https://commons.wikimedia.org/w/api.php"
PHOTO_EXT = re.compile(r"\.(jpe?g|png|webp)$", re.I)
TAG_RE = re.compile(r"<[^>]+>")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _clean(s: str | None) -> str | None:
    if not s:
        return None
    s = html.unescape(TAG_RE.sub("", s)).strip()
    return s or None


async def category_files(category: str, limit: int = 60) -> list[str]:
    try:
        d = await http.get_json(API, params={
            "action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}",
            "cmtype": "file", "cmlimit": limit, "format": "json",
        }, timeout=5.0)
    except Exception:
        return []
    return [m["title"] for m in d.get("query", {}).get("categorymembers", []) if PHOTO_EXT.search(m["title"])]


async def depicts_files(qid: str, limit: int = 40) -> list[str]:
    try:
        d = await http.get_json(API, params={
            "action": "query", "list": "search", "srsearch": f"haswbstatement:P180={qid}",
            "srnamespace": 6, "srlimit": limit, "format": "json",
        }, timeout=5.0)
    except Exception:
        return []
    return [m["title"] for m in d.get("query", {}).get("search", []) if PHOTO_EXT.search(m["title"])]


async def geosearch_files(lat: float, lon: float, radius_m: int = 800, limit: int = 60) -> list[dict]:
    try:
        d = await http.get_json(API, params={
            "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": min(radius_m, 10000),
            "gsnamespace": 6, "gslimit": limit, "format": "json",
        }, timeout=5.0)
    except Exception:
        return []
    return [{"title": m["title"], "lat": m["lat"], "lon": m["lon"], "dist": m.get("dist")}
            for m in d.get("query", {}).get("geosearch", []) if PHOTO_EXT.search(m["title"])]


async def image_info(titles: list[str], width: int = 800) -> dict[str, dict]:
    """Batch metadata for up to 50 files per request."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        try:
            d = await http.get_json(API, params={
                "action": "query", "titles": "|".join(batch), "prop": "imageinfo",
                "iiprop": "url|extmetadata|timestamp|size|user|mime", "iiurlwidth": width,
                "iiextmetadatafilter": "DateTimeOriginal|Artist|LicenseShortName|ImageDescription|GPSLatitude|GPSLongitude|Credit|ObjectName",
                "format": "json",
            }, timeout=6.0)
        except Exception:
            continue
        pages = d.get("query", {}).get("pages", {})
        normalized = d.get("query", {}).get("normalized", [])
        back = {n["to"]: n["from"] for n in normalized}
        for p in pages.values():
            title = p.get("title")
            ii = (p.get("imageinfo") or [None])[0]
            if not title or not ii:
                continue
            mime = ii.get("mime", "")
            if not mime.startswith("image/") or mime in ("image/svg+xml", "image/gif"):
                continue
            meta = {k: v.get("value") for k, v in (ii.get("extmetadata") or {}).items()}
            out[back.get(title, title)] = {
                "title": title,
                "url": ii.get("url"),
                "thumb": ii.get("thumburl") or ii.get("url"),
                "width": ii.get("width"),
                "height": ii.get("height"),
                "upload": ii.get("timestamp"),
                "user": ii.get("user"),
                "date_original": meta.get("DateTimeOriginal"),
                "artist": _clean(meta.get("Artist")),
                "license": _clean(meta.get("LicenseShortName")),
                "description": _clean(meta.get("ImageDescription")),
                "credit": _clean(meta.get("Credit")),
                "lat": _to_float(meta.get("GPSLatitude")),
                "lon": _to_float(meta.get("GPSLongitude")),
            }
    return out


def _to_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_candidates(infos: dict[str, dict], source: str, is_city: bool = False,
                  geo_hints: dict[str, tuple[float, float]] | None = None) -> list[PhotoCandidate]:
    out: list[PhotoCandidate] = []
    for title, info in infos.items():
        if not info.get("thumb"):
            continue
        date, date_source = None, None
        m = DATE_RE.search(info.get("date_original") or "")
        if m:
            date, date_source = m.group(0), "exif"
        elif info.get("upload"):
            date, date_source = info["upload"][:10], "upload"
        lat, lon = info.get("lat"), info.get("lon")
        if (lat is None or lon is None) and geo_hints and title in geo_hints:
            lat, lon = geo_hints[title]
        name = title.split(":", 1)[-1]
        out.append(PhotoCandidate(
            url=info["thumb"],
            page_url="https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_")),
            source=source,
            title=name.rsplit(".", 1)[0].replace("_", " "),
            text=" ".join(x for x in [name, info.get("description"), info.get("credit")] if x),
            author=info.get("artist") or info.get("user"),
            license=info.get("license"),
            date=date, date_source=date_source,
            lat=lat, lon=lon,
            width=info.get("width"), height=info.get("height"),
            is_city=is_city,
        ))
    return out
