"""Open Graph preview card (1200x630) rendered with Pillow: cover photo, name, city, verified count.
Fonts fall back to Arial when the product fonts are not installed on the server."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import settings
from ..models import CATEGORIES, Profile

W, H = 1200, 630
BLUE, INK, MUTED, PAPER = (29, 78, 216), (10, 10, 10), (107, 114, 128), (247, 248, 250)
_FONT_DIRS = [Path.home() / "AppData/Local/Microsoft/Windows/Fonts", Path("C:/Windows/Fonts"), Path("/usr/share/fonts"), Path("/usr/share/fonts/truetype/dejavu")]


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = (["Manrope-Bold.ttf", "Manrope-ExtraBold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["Inter-Regular.ttf", "Inter.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for d in _FONT_DIRS:
        for n in names:
            f = d / n
            if f.exists():
                return ImageFont.truetype(str(f), size)
    return ImageFont.load_default(size)  # type: ignore[return-value]


def description(p: Profile | None, row: dict | None) -> str:
    if p:
        verified = sum(1 for ph in p.photos if ph.level == "verified")
        cats = sum(1 for c in CATEGORIES if p.categories.get(c) and (p.categories[c].verified or p.categories[c].likely))
        return f"{verified} подтверждённых фото в {cats} категориях, источник и показатель достоверности у каждого · климат, город, 3D-кампус"
    return "Проверенный визуальный профиль университета: фото с источниками, климат, город, 3D-кампус"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and draw.textlength(lines[-1], font=font) > width - 30:
        lines[-1] = lines[-1][:-1].rstrip() + "…"
    return lines


def render(p: Profile | None, row: dict | None) -> bytes:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    # faint topographic rings, the product's background motif
    for r in range(80, 900, 46):
        d.ellipse((980 - r, 520 - r, 980 + r, 520 + r), outline=(228, 231, 236), width=1)

    cover = None
    if p:
        for ph in p.photos:
            f = settings.thumbs_dir / f"{ph.id}.jpg"
            if ph.level == "verified" and f.exists():
                cover = f
                break
    cw = 640
    if cover:
        im = Image.open(cover).convert("RGB")
        scale = max(cw / im.width, H / im.height)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        left, top = (im.width - cw) // 2, (im.height - H) // 2
        img.paste(im.crop((left, top, left + cw, top + H)), (0, 0))
    else:
        d.rectangle((0, 0, cw, H), fill=(14, 22, 45))
        d.ellipse((120, 130, 520, 530), fill=(24, 48, 120), outline=(90, 130, 220), width=3)
        cw = 640

    x = cw + 48
    name = (p.university.name if p else None) or (row.get("ru") or row.get("en") if row else "") or ""
    city = (p.university.city if p else None) or (row.get("city") if row else None)
    country = p.university.country if p else None
    d.rounded_rectangle((x, 56, x + 26, 82), radius=13, fill=BLUE)
    d.text((x + 36, 54), "CampusLens", font=_font(True, 24), fill=INK)
    y = 130
    for line in _wrap(d, name, _font(True, 46), W - x - 48, 3):
        d.text((x, y), line, font=_font(True, 46), fill=INK)
        y += 56
    sub = " · ".join([s for s in (city, country) if s])
    if sub:
        d.text((x, y + 6), sub, font=_font(False, 26), fill=MUTED)
        y += 46
    if p:
        verified = sum(1 for ph in p.photos if ph.level == "verified")
        likely = sum(1 for ph in p.photos if ph.level == "likely")
        cats = sum(1 for c in CATEGORIES if p.categories.get(c) and (p.categories[c].verified or p.categories[c].likely))
        stats = [(str(verified), "подтверждено"), (str(likely), "вероятно"), (f"{cats}/8", "категорий")]
        sx = x
        for num, label in stats:
            d.text((sx, H - 190), num, font=_font(True, 54), fill=BLUE)
            d.text((sx, H - 124), label, font=_font(False, 20), fill=MUTED)
            sx += max(150, int(d.textlength(num, font=_font(True, 54))) + 60)
    d.text((x, H - 64), "источник и показатель достоверности у каждого фото", font=_font(False, 19), fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
