"""Social channels of a university as photo sources — real content, not links.

- links are discovered on the official homepage (instagram / facebook / telegram / youtube / vk / tiktok);
- Telegram: public channel preview t.me/s/<channel> (no login), photos + captions + post links;
- YouTube: channel RSS (channel id from the channel page) → video stills with titles and dates;
- Instagram: public mirrors (best effort; they may block), post links point to instagram.com;
- VK: official API with a free service token (VK_SERVICE_TOKEN), else skipped.
Facebook and TikTok need a login and are not fetched — only shown as links.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ... import http
from ...config import settings
from ...models import PhotoCandidate

log = logging.getLogger("campuslens.social")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
PATTERNS = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,40})/?(?:[?#]|$)", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.)?facebook\.com/([A-Za-z0-9_.-]{3,60})/?(?:[?#]|$)", re.I),
    "telegram": re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,40})/?(?:[?#]|$)", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(@[\w.-]{2,60}|channel/[\w-]{10,40}|c/[\w.-]{2,60}|user/[\w.-]{2,60})", re.I),
    "vk": re.compile(r"https?://(?:www\.|m\.)?vk\.com/([A-Za-z0-9_.]{3,60})/?(?:[?#]|$)", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@([\w.]{2,40})", re.I),
}
NOT_PROFILE = {
    "instagram": {"p", "explore", "reel", "reels", "accounts", "share", "stories", "direct"},
    "facebook": {"sharer", "share.php", "sharer.php", "plugins", "dialog", "login", "groups", "events", "hashtag", "profile.php"},
    "telegram": {"share", "joinchat", "addstickers", "iv"},
    "vk": {"share.php", "widget", "away.php", "dev", "feed", "video", "wall", "id0"},
    "youtube": set(),
    "tiktok": set(),
}
BG_URL = re.compile(r"background-image:\s*url\(['\"]?([^'\")]+)['\"]?\)")


def discover(html: str, base: str) -> dict[str, str]:
    """Social profile links present on a page (first hit per network, usually the footer icons)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        for net, rx in PATTERNS.items():
            if net in out:
                continue
            m = rx.match(href)
            if m and m.group(1).split("/")[0].lower() not in NOT_PROFILE[net]:
                out[net] = m.group(0).rstrip("/")
    return out


# ---------------------------------------------------------------- Telegram
async def telegram(url: str, limit: int = 30) -> list[PhotoCandidate]:
    m0 = PATTERNS["telegram"].match(url)
    if not m0:
        return []
    channel = m0.group(1)
    out: list[PhotoCandidate] = []
    before: int | None = None
    for _ in range(2):
        params = {"before": before} if before else None
        try:
            r = await http.get(f"https://t.me/s/{channel}", params=params, headers={"User-Agent": UA}, timeout=6.0)
        except Exception as e:  # noqa: BLE001
            log.info("telegram failed: %s", e)
            break
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        msgs = soup.select("div.tgme_widget_message")
        if not msgs:
            break
        ids: list[int] = []
        for m in msgs:
            post = m.get("data-post", "")
            if "/" in post:
                try:
                    ids.append(int(post.rsplit("/", 1)[1]))
                except ValueError:
                    pass
            text_el = m.select_one(".tgme_widget_message_text")
            text = text_el.get_text(" ", strip=True) if text_el else ""
            link_el = m.select_one("a.tgme_widget_message_date")
            page = link_el["href"] if link_el and link_el.get("href") else f"https://t.me/{post}"
            time_el = m.select_one("time[datetime]")
            date = time_el["datetime"][:7] if time_el and time_el.get("datetime") else None
            for ph in m.select("a.tgme_widget_message_photo_wrap"):
                mm = BG_URL.search(ph.get("style", ""))
                if not mm:
                    continue
                out.append(PhotoCandidate(
                    url=mm.group(1), page_url=page, source="telegram", title=(text[:120] or f"@{channel}"),
                    text=f"{text} @{channel}", author=f"@{channel}", license="© Telegram-канал вуза",
                    date=date, date_source="post" if date else None))
        if len(out) >= limit or not ids:
            break
        before = min(ids)
    return out[:limit]


# ---------------------------------------------------------------- YouTube
VIDEO_ID = re.compile(r'"videoId":"([\w-]{11})"')
CONTENT_ID = re.compile(r'"contentId":"([\w-]{11})"')
TITLE_CONTENT = re.compile(r'"title":\{"content":"((?:[^"\\\\]|\\\\.){1,200}?)"')
TITLE_NEAR = re.compile(r'"title":\{"runs":\[\{"text":"((?:[^"\\\\]|\\\\.){1,200}?)"')
LABEL_NEAR = re.compile(r'"title":\{"accessibility":\{"accessibilityData":\{"label":"((?:[^"\\\\]|\\\\.){1,200}?)"')


async def youtube(url: str, limit: int = 15) -> list[PhotoCandidate]:
    """Latest uploads of the channel from its /videos page (the RSS feed is not served to non-browser clients)."""
    try:
        r = await http.get(url.rstrip("/") + "/videos", headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=6.0,
                           params={"hl": "en"})
    except Exception as e:  # noqa: BLE001
        log.info("youtube failed: %s", e)
        return []
    html = r.text
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html)
    name = re.search(r'<meta property="og:title" content="([^"]{1,120})"', html)
    channel = (name.group(1) if name else "YouTube").replace(" - YouTube", "")
    # titles live in lockupViewModel blocks: "contentId":"<id>" … "title":{"content":"…"}
    titles: dict[str, str] = {}
    for cm in CONTENT_ID.finditer(html):
        tm = TITLE_CONTENT.search(html, cm.end(), cm.end() + 4000)
        if tm and cm.group(1) not in titles:
            titles[cm.group(1)] = tm.group(1).encode("utf-8").decode("unicode_escape", "ignore")
    out: list[PhotoCandidate] = []
    seen: set[str] = set()
    for vm in VIDEO_ID.finditer(html):
        vid = vm.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        title = titles.get(vid, "")
        if not title:
            window = html[max(0, vm.start() - 1200): vm.end() + 1500]
            tm = TITLE_NEAR.search(window) or LABEL_NEAR.search(window)
            title = tm.group(1).encode("utf-8").decode("unicode_escape", "ignore") if tm else ""
        out.append(PhotoCandidate(
            url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", page_url=f"https://www.youtube.com/watch?v={vid}",
            source="youtube", title=title[:120] or None, text=f"{title} {channel}", author=channel,
            license="© YouTube-канал вуза (кадр видео)"))
        if len(out) >= limit:
            break
    if not out and m:
        log.info("youtube: no videos parsed for channel %s", m.group(1))
    return out


# ---------------------------------------------------------------- Instagram (public mirrors, best effort)
MIRRORS = ["https://imginn.com/{h}/", "https://www.picuki.com/profile/{h}", "https://dumpor.io/v/{h}"]


async def instagram(url: str, limit: int = 24) -> list[PhotoCandidate]:
    m0 = PATTERNS["instagram"].match(url)
    if not m0:
        return []
    handle = m0.group(1)
    for tpl in MIRRORS:
        try:
            r = await http.get(tpl.format(h=handle), headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=6.0)
        except Exception as e:  # noqa: BLE001
            log.info("instagram mirror failed: %s", e)
            continue
        if r.status_code != 200 or ("cdninstagram" not in r.text and "fbcdn" not in r.text):
            continue
        soup = BeautifulSoup(r.text, "lxml")
        out: list[PhotoCandidate] = []
        seen: set[str] = set()
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not re.search(r"cdninstagram|fbcdn", src) or src in seen or re.search(r"/s150x150/|profile_pic|/t51\.2885-19/", src):
                continue
            seen.add(src)
            a = img.find_parent("a")
            href = a.get("href", "") if a else ""
            sc = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]{5,})", href)
            page = f"https://www.instagram.com/p/{sc.group(1)}/" if sc else f"https://www.instagram.com/{handle}/"
            alt = (img.get("alt") or "").strip()
            out.append(PhotoCandidate(url=src, page_url=page, source="instagram", title=alt[:120] or f"@{handle}",
                                      text=f"{alt} @{handle}", author=f"@{handle}", license="© Instagram вуза"))
            if len(out) >= limit:
                break
        if out:
            return out
    return []


# ---------------------------------------------------------------- VK (official API, service token)
async def vk(url: str, limit: int = 40) -> list[PhotoCandidate]:
    if not settings.vk_service_token:
        return []
    m0 = PATTERNS["vk"].match(url)
    if not m0:
        return []
    group = m0.group(1)
    try:
        r = await http.get("https://api.vk.com/method/wall.get", params={
            "domain": group, "count": 50, "v": "5.199", "access_token": settings.vk_service_token}, timeout=6.0)
        items = r.json().get("response", {}).get("items", [])
    except Exception as e:  # noqa: BLE001
        log.info("vk failed: %s", e)
        return []
    out: list[PhotoCandidate] = []
    for it in items:
        text = (it.get("text") or "")[:200]
        page = f"https://vk.com/wall{it.get('owner_id')}_{it.get('id')}"
        for att in it.get("attachments", []):
            if att.get("type") != "photo":
                continue
            sizes = att["photo"].get("sizes", [])
            if not sizes:
                continue
            best = max(sizes, key=lambda s: s.get("width", 0))
            out.append(PhotoCandidate(url=best["url"], page_url=page, source="vk", title=text[:120] or group,
                                      text=f"{text} {group}", author=group, license="© группа VK вуза"))
            if len(out) >= limit:
                return out
    return out
