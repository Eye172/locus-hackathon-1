"""Signal triangulation: every photo gets an explainable confidence score."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..config import settings
from ..geo import CampusGeom, haversine_km
from ..models import Photo, Signal

SOURCE_PRIOR: dict[str, float] = {
    "commons_depicts": 0.45,
    "wikipedia": 0.40,
    "mapillary": 0.40,
    "official": 0.35,
    "commons_cat": 0.35,
    "places": 0.35,
    "flickr": 0.20,
    "commons_geo": 0.20,
    "city_article": 0.45,
    "city_cat": 0.35,
}
SOURCE_SIGNAL_LABEL: dict[str, str] = {
    "commons_depicts": "Structured data Commons: «изображает» этот вуз",
    "wikipedia": "Файл используется в статье Википедии о вузе",
    "mapillary": "Уличный снимок Mapillary с геопривязкой внутри кампуса",
    "official": "Размещено на официальном сайте вуза",
    "commons_cat": "Файл в категории вуза на Wikimedia Commons",
    "places": "Фото привязано к объекту вуза в Google Places",
    "flickr": "Геофото Flickr в границах кампуса",
    "commons_geo": "Файл Commons с геометкой рядом с кампусом",
    "city_article": "Файл используется в статье о городе",
    "city_cat": "Файл в категории города на Commons",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^\w]+", " ", s).strip()


@dataclass
class VerifyContext:
    geom: CampusGeom
    aliases: list[str]
    city_center: tuple[float, float] | None = None
    flags: dict[str, int] = field(default_factory=dict)
    alias_norms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.alias_norms = sorted({norm(a) for a in self.aliases if len(norm(a)) >= 4}, key=len, reverse=True)


def score(p: Photo, ctx: VerifyContext, text: str, is_city: bool) -> None:
    signals: list[Signal] = []

    # 1. source priors (one per distinct source)
    for src in p.sources:
        w = SOURCE_PRIOR.get(src, 0.15)
        signals.append(Signal(key=f"src_{src}", label=SOURCE_SIGNAL_LABEL.get(src, src), weight=w))

    # 2. geo
    if p.lat is not None and p.lon is not None:
        if is_city and ctx.city_center:
            d_km = haversine_km(p.lat, p.lon, *ctx.city_center)
            p.geo_distance_m = round(d_km * 1000)
            if d_km <= 15:
                p.geo_inside = True
                signals.append(Signal(key="geo_city", label="Геометка в пределах города", weight=0.20,
                                      value=f"{d_km:.1f} км от центра"))
            else:
                p.geo_inside = False
                signals.append(Signal(key="geo_far", label="Геометка далеко от города", weight=-0.25,
                                      value=f"{d_km:.0f} км"))
        else:
            d = ctx.geom.distance_m(p.lat, p.lon)
            p.geo_distance_m = round(d)
            if d == 0:
                p.geo_inside = True
                signals.append(Signal(key="geo_inside", label="Геометка внутри границ кампуса", weight=0.30,
                                      value="внутри" if ctx.geom.mode == "polygon" else "в радиусе 500 м"))
            elif d <= 300:
                p.geo_inside = False
                signals.append(Signal(key="geo_near", label="Геометка рядом с кампусом", weight=0.10,
                                      value=f"{d:.0f} м от границы"))
            elif d > 1000:
                p.geo_inside = False
                signals.append(Signal(key="geo_far", label="Геометка далеко от кампуса", weight=-0.25,
                                      value=f"{d/1000:.1f} км"))
            else:
                p.geo_inside = False

    # 3. semantics
    best = max(p.category_scores.values()) if p.category_scores else 0.0
    if p.junk_soft:
        signals.append(Signal(key="clip_text_heavy", label="Много текста в кадре (баннер/вывеска), источник надёжный",
                              weight=-0.10, value=f"{p.junk_score:.0%}"))
    elif p.junk_score > 0.5:
        signals.append(Signal(key="clip_junk", label="Похоже на не-фотографию (логотип, карта, документ…)",
                              weight=-0.30, value=f"{p.junk_score:.0%}"))
    else:
        signals.append(Signal(key="clip_match", label="Семантика CLIP соответствует категории", weight=round(0.25 * best, 3),
                              value=f"{p.category} {best:.0%}"))

    if p.stock_hint:
        signals.append(Signal(key="stock_hint", label="В адресе или подписи есть признак стокового сайта", weight=-0.20))

    # 4. name / alias match in caption, alt, page title or path
    t = norm(text)
    hit = next((a for a in ctx.alias_norms if a in t), None)
    if hit:
        signals.append(Signal(key="name_match", label="Название вуза в подписи или источнике", weight=0.15, value=hit))

    # 5. cross-source
    if p.sources_count > 1:
        signals.append(Signal(key="cross_source", label=f"Найдено в {p.sources_count} источниках",
                              weight=min(0.20, 0.10 * (p.sources_count - 1))))

    # 6. date
    if p.date:
        signals.append(Signal(key="has_date", label="Есть дата", weight=0.03, value=p.date))

    # 7. user flags
    n = ctx.flags.get(p.id, 0)
    if n:
        signals.append(Signal(key="user_flag", label="Помечено пользователями как «не этот вуз»", weight=-0.5, value=str(n)))

    conf = max(0.0, min(1.0, sum(s.weight for s in signals)))
    p.signals = signals
    p.confidence = round(conf, 3)
    p.level = ("verified" if conf >= settings.verified_threshold
               else "likely" if conf >= settings.likely_cut() else "unverified")
