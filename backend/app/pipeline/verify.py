"""Signal triangulation: every photo gets an explainable confidence score.

Provenance (where the file was found), geometry (geotag vs campus outline), the AI inspector's verdict (what the
photo actually shows, compared with a reference photo of the university), similarity to that reference, captions,
cross-source copies, dates and user flags. A trusted source alone is not proof: a city-article photo is a city photo,
a street frame next to the campus is not the campus, a stock photo on the official site is still stock.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..config import settings
from ..geo import CampusGeom, haversine_km
from ..models import CATEGORIES, AiVerdict, Photo, Signal
from .ai_inspector import ERA_RU, FLAG_RU

SOURCE_PRIOR: dict[str, float] = {
    "commons_depicts": 0.45,
    "wikipedia": 0.40,
    "official": 0.35,
    "commons_cat": 0.35,
    "places": 0.35,
    "telegram": 0.35,
    "instagram": 0.35,
    "vk": 0.35,
    "tiktok": 0.30,
    "youtube": 0.30,
    "mapillary": 0.25,
    "flickr": 0.20,
    "commons_geo": 0.20,
    "city_article": 0.45,
    "city_cat": 0.35,
    "web_image": 0.20,
    "commons_search": 0.20,
    "openverse": 0.15,
    "tiktok_search": 0.10,
    "youtube_search": 0.15,
}
SOURCE_SIGNAL_LABEL: dict[str, str] = {
    "commons_depicts": "Structured data Commons: «изображает» этот вуз",
    "wikipedia": "Файл используется в статье Википедии о вузе",
    "mapillary": "Уличный снимок Mapillary с геопривязкой у кампуса",
    "official": "Размещено на официальном сайте вуза",
    "commons_cat": "Файл в категории вуза на Wikimedia Commons",
    "places": "Фото привязано к объекту вуза в Google Places",
    "flickr": "Геофото Flickr у кампуса",
    "commons_geo": "Файл Commons с геометкой рядом с кампусом",
    "city_article": "Файл используется в статье о городе",
    "city_cat": "Файл в категории города на Commons",
    "telegram": "Пост в официальном Telegram-канале вуза (ссылка с сайта)",
    "instagram": "Публикация в официальном Instagram вуза (ссылка с сайта)",
    "vk": "Пост в официальной группе VK вуза",
    "tiktok": "Видео в официальном TikTok вуза (ссылка с сайта)",
    "youtube": "Кадр видео с официального YouTube-канала вуза",
    "web_image": "Google Картинки по запросу с названием вуза",
    "commons_search": "Файл Commons, в описании которого есть название вуза",
    "openverse": "Фото с открытой лицензией (Openverse) по названию вуза",
    "tiktok_search": "Видео в TikTok по запросу с названием вуза (автор не обязательно вуз)",
    "youtube_search": "Кадр видео на YouTube по запросу с названием вуза (автор не обязательно вуз)",
}
CITY_SOURCES = {"city_article", "city_cat"}
SEARCH_SOURCES = {"web_image", "tiktok_search", "youtube_search", "openverse", "commons_search", "external"}
REJECT_FLAGS = {"illustration", "stock", "screenshot", "logo", "collage"}
SOFT_FLAGS = {"banner": -0.20, "crop": -0.10, "official_meeting": -0.25, "portrait": -0.08, "text": -0.05}
CITY_Q_WEIGHT = {3: 0.12, 2: 0.08, 1: -0.12, 0: -0.30}
REL_WEIGHT = {3: 0.30, 2: 0.25, 1: -0.15, 0: -0.40}


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


def _reject(p: Photo, reason: str) -> None:
    if not p.rejected:
        p.rejected, p.reject_reason = True, reason


def _geo(p: Photo, ctx: VerifyContext, as_city: bool, signals: list[Signal]) -> None:
    if p.lat is None or p.lon is None:
        return
    if as_city and ctx.city_center:
        d_km = haversine_km(p.lat, p.lon, *ctx.city_center)
        p.geo_distance_m = round(d_km * 1000)
        p.geo_inside = d_km <= 15
        if p.geo_inside:
            signals.append(Signal(key="geo_city", label="Геометка в пределах города", weight=0.20, value=f"{d_km:.1f} км от центра"))
        else:
            signals.append(Signal(key="geo_far", label="Геометка далеко от города", weight=-0.25, value=f"{d_km:.0f} км"))
        return
    d = ctx.geom.distance_m(p.lat, p.lon)
    p.geo_distance_m = round(d)
    p.geo_inside = d == 0
    polygon = ctx.geom.mode == "polygon"
    if d == 0:
        # a 500 m circle around a point is not a campus boundary: it also covers the neighbours
        signals.append(Signal(key="geo_inside", label="Геометка внутри границ кампуса" if polygon else "Геометка в радиусе 500 м от вуза",
                              weight=0.30 if polygon else 0.12, value="внутри полигона OSM" if polygon else "границы кампуса неизвестны"))
    elif d <= 300:
        signals.append(Signal(key="geo_near", label="Геометка рядом с кампусом", weight=0.08, value=f"{d:.0f} м от границы"))
    elif d > 1000:
        signals.append(Signal(key="geo_far", label="Геометка далеко от кампуса", weight=-0.25, value=f"{d/1000:.1f} км"))


def _apply_verdict(p: Photo, v: AiVerdict, as_city: bool, name_hit: bool, signals: list[Signal]) -> bool:
    """Adds the inspector's signals; returns the (possibly updated) as_city flag.

    A photo from a city article stays a city photo unless its own caption names the university: the model
    sometimes takes an old mansion for the campus when the reference photo is itself historical."""
    who = f"ИИ-инспектор ({v.model})" if v.model else "ИИ-инспектор"
    why = f" · {v.why}" if v.why else ""
    p.ai, p.quality = v, v.q
    if v.cat in CATEGORIES and v.cat != "none":
        if v.cat != p.category:
            p.secondary = p.category  # type: ignore[assignment]
        p.category = v.cat  # type: ignore[assignment]

    if v.place == "not_photo":
        signals.append(Signal(key="ai_not_photo", label=f"{who}: это не фотография", weight=-0.6, value=v.why or None))
        _reject(p, f"не фотография{why}")
    elif v.place == "other_place":
        signals.append(Signal(key="ai_other_place", label=f"{who}: другое место, не этот вуз", weight=-0.6, value=v.why or None))
        _reject(p, f"другое место{why}")
    elif v.place == "city" or (as_city and (v.place != "this_university" or not name_hit)):
        # for city views the useful question is "is it a clear view of the city", not "is it the university"
        as_city = True
        p.category = "city"
        signals.append(Signal(key="ai_city", label=f"{who}: вид города", weight=CITY_Q_WEIGHT[v.q],
                              value=f"наглядность {v.q}/3{why}"))
    elif v.place == "this_university":
        as_city = False  # a city-article photo whose caption names the university shows the campus
        signals.append(Signal(key=f"ai_rel_{v.rel}", label=f"{who}: {'это' if v.rel >= 2 else 'возможно, не'} этот вуз",
                              weight=REL_WEIGHT[v.rel], value=f"уверенность {v.rel}/3{why}"))
    else:  # unknown
        signals.append(Signal(key="ai_unknown", label=f"{who}: принадлежность не определить", weight=-0.05, value=v.why or None))

    hard = [f for f in v.flags if f in REJECT_FLAGS]
    if hard:
        signals.append(Signal(key="ai_flag_" + hard[0], label=f"{who}: {FLAG_RU[hard[0]]}", weight=-0.5, value=v.why or None))
        _reject(p, f"{FLAG_RU[hard[0]]}{why}")
    for f in v.flags:
        if f in SOFT_FLAGS:
            signals.append(Signal(key=f"ai_flag_{f}", label=f"{who}: {FLAG_RU[f]}", weight=SOFT_FLAGS[f]))
    if v.q == 0:
        signals.append(Signal(key="ai_q0", label=f"{who}: малоинформативный кадр (полоса, фрагмент, размытие)", weight=-0.3))
        _reject(p, f"малоинформативный кадр{why}")
    elif v.q == 3:
        signals.append(Signal(key="ai_q3", label=f"{who}: наглядный кадр", weight=0.03))
    if v.era in ERA_RU and (not p.date or p.date_source == "last-modified"):
        # a web server's Last-Modified is when the file was uploaded, not when the photo was taken
        p.date_estimate = v.era
        if v.era in ("2000s", "older"):
            p.outdated = True
            if p.date_source == "last-modified":
                p.date = p.date_source = p.year = None
    return as_city


def score(p: Photo, ctx: VerifyContext, text: str, is_city: bool, verdict: AiVerdict | None = None,
          ref_sim: float | None = None) -> None:
    signals: list[Signal] = []
    as_city = is_city or p.source in CITY_SOURCES
    if as_city:
        p.category = "city"

    t = norm(text)
    hit = next((a for a in ctx.alias_norms if a in t), None)

    # 1. provenance (one per distinct source)
    for src in p.sources:
        signals.append(Signal(key=f"src_{src}", label=SOURCE_SIGNAL_LABEL.get(src, src), weight=SOURCE_PRIOR.get(src, 0.15)))

    # 2. what the photo shows: the inspector's verdict first, CLIP as a weaker hint
    if verdict is not None:
        as_city = _apply_verdict(p, verdict, as_city, bool(hit), signals)
    best = p.category_scores.get(p.category, max(p.category_scores.values()) if p.category_scores else 0.0)
    clip_w = 0.08 if verdict is not None else 0.25
    if p.junk_soft:
        signals.append(Signal(key="clip_text_heavy", label="CLIP: много текста в кадре (баннер/вывеска)", weight=-0.10,
                              value=f"{p.junk_score:.0%}"))
    elif p.junk_score > 0.5:
        signals.append(Signal(key="clip_junk", label="CLIP: похоже на не-фотографию (логотип, карта, документ…)", weight=-0.30,
                              value=f"{p.junk_score:.0%}"))
    else:
        signals.append(Signal(key="clip_match", label="CLIP: семантика соответствует категории", weight=round(clip_w * best, 3),
                              value=f"{p.category} {best:.0%}"))
    if p.stock_hint:
        signals.append(Signal(key="stock_hint", label="В адресе или подписи есть признак стокового сайта", weight=-0.20))

    # 3. geometry (relative to the city centre for city photos)
    _geo(p, ctx, as_city, signals)

    # 4. looks like the reference photo of the university (exteriors only; interiors never do)
    if ref_sim is not None:
        p.ref_similarity = round(ref_sim, 3)
        if not as_city and ref_sim >= 0.82:
            signals.append(Signal(key="ref_similar", label="Похоже на эталонное фото вуза (CLIP)", weight=0.10, value=f"{ref_sim:.0%}"))

    # 5. name / alias in caption, alt, page title or path
    if hit and not as_city:
        signals.append(Signal(key="name_match", label="Название вуза в подписи или источнике", weight=0.15, value=hit))
    elif not hit and not as_city and set(p.sources) <= SEARCH_SOURCES:
        # a search hit whose page never names the university: abbreviations collide (КГУ = Kokshetau or Kurgan)
        signals.append(Signal(key="no_name", label="Найдено поиском, но на странице нет названия вуза", weight=-0.15))
        if verdict is not None and verdict.place == "this_university" and verdict.rel < 3:
            _reject(p, "найдено поиском: на странице нет названия вуза, и ИИ не узнал в фото именно этот вуз")

    # 6. copies in several sources
    if p.sources_count > 1:
        signals.append(Signal(key="cross_source", label=f"Найдено в {p.sources_count} источниках",
                              weight=min(0.20, 0.10 * (p.sources_count - 1))))

    # 7. date
    if p.date:
        signals.append(Signal(key="has_date", label="Есть дата", weight=0.03, value=p.date))

    # 8. user flags
    n = ctx.flags.get(p.id, 0)
    if n:
        signals.append(Signal(key="user_flag", label="Помечено пользователями как «не этот вуз»", weight=-0.5, value=str(n)))

    if verdict is None and settings.active_llm() != "none":
        signals.append(Signal(key="ai_pending", label="ИИ-инспектор не успел проверить фото", weight=0.0))
        if set(p.sources) <= SEARCH_SOURCES:
            # a search hit is only a lead: without the inspector's look it is not shown as the university
            _reject(p, "найдено поиском, но ИИ-инспектор не успел проверить фото в отведённое время")

    conf = max(0.0, min(1.0, sum(s.weight for s in signals)))
    p.signals = signals
    p.confidence = round(conf, 3)
    p.level = ("verified" if conf >= settings.verified_threshold
               else "likely" if conf >= settings.likely_cut() else "unverified")
    if p.rejected:
        p.level = "unverified"
    elif p.level == "unverified":
        _reject(p, f"низкая уверенность ({p.confidence:.0%}): недостаточно сигналов принадлежности")
