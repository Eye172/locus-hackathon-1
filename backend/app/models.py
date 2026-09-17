"""Pydantic schemas shared by the pipeline, the cache and the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["campus", "dormitory", "classroom", "library", "lab", "sports", "student_life", "city"]
CATEGORIES: list[str] = ["campus", "dormitory", "classroom", "library", "lab", "sports", "student_life", "city"]
CATEGORY_LABELS: dict[str, dict[str, str]] = {
    "campus": {"ru": "Кампус", "en": "Campus", "kk": "Кампус"},
    "dormitory": {"ru": "Общежитие", "en": "Dormitory", "kk": "Жатақхана"},
    "classroom": {"ru": "Аудитории", "en": "Classrooms", "kk": "Аудиториялар"},
    "library": {"ru": "Библиотека", "en": "Library", "kk": "Кітапхана"},
    "lab": {"ru": "Лаборатории", "en": "Labs", "kk": "Зертханалар"},
    "sports": {"ru": "Спорт", "en": "Sports", "kk": "Спорт"},
    "student_life": {"ru": "Студенческая жизнь", "en": "Student life", "kk": "Студенттік өмір"},
    "city": {"ru": "Город", "en": "City", "kk": "Қала"},
}

Source = Literal["official", "commons_cat", "commons_depicts", "commons_geo", "wikipedia", "city_article",
                 "city_cat", "mapillary", "flickr", "places"]
SOURCE_LABELS: dict[str, str] = {
    "official": "Официальный сайт",
    "commons_cat": "Wikimedia Commons (категория)",
    "commons_depicts": "Wikimedia Commons (depicts)",
    "commons_geo": "Wikimedia Commons (геопоиск)",
    "wikipedia": "Статья Википедии",
    "city_article": "Статья о городе",
    "city_cat": "Wikimedia Commons (город)",
    "mapillary": "Mapillary",
    "flickr": "Flickr",
    "places": "Google Places",
    "telegram": "Telegram-канал вуза",
    "youtube": "YouTube-канал вуза (кадры видео)",
    "instagram": "Instagram вуза",
    "instagram_tagged": "Instagram: посты, где отметили вуз",
    "vk": "Группа VK вуза",
    "vk_geo": "ВКонтакте: фото с геометкой у кампуса",
    "external": "Внешний коллектор",
    "web_image": "Google Картинки (поиск)",
    "map_review": "Отзывы на Google Картах (фото посетителей)",
    "commons_search": "Wikimedia Commons (поиск по названию)",
    "openverse": "Openverse (открытые лицензии)",
    "tiktok": "TikTok вуза",
    "tiktok_search": "TikTok (поиск по названию)",
    "youtube_search": "YouTube (поиск по названию)",
}
BROCHURE_SOURCES = {"official"}


class ExternalCandidates(BaseModel):
    """Payload accepted from another collector (e.g. the Node/Next.js part of the project)."""
    qid: str
    collector: str = "external"
    candidates: list["PhotoCandidate"]


class Candidate(BaseModel):
    qid: str
    label: str
    description: str | None = None
    city: str | None = None
    country: str | None = None
    logo_url: str | None = None
    score: float = 0.0
    origin: Literal["index", "wikidata", "web"] = "wikidata"


class University(BaseModel):
    qid: str
    name: str
    names: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    website: str | None = None
    commons_category: str | None = None
    wikipedia: dict[str, str] = Field(default_factory=dict)  # lang -> title
    lat: float | None = None
    lon: float | None = None
    coord_source: str | None = None
    city: str | None = None
    city_qid: str | None = None
    city_lat: float | None = None
    city_lon: float | None = None
    city_commons_category: str | None = None
    city_population: int | None = None
    city_wikipedia: dict[str, str] = Field(default_factory=dict)
    country: str | None = None
    founded: int | None = None
    students: int | None = None
    logo_url: str | None = None
    image_url: str | None = None
    summary: str | None = None
    summary_url: str | None = None
    social: dict[str, str] = Field(default_factory=dict)  # network -> profile url, found on the official site


class Building(BaseModel):
    osm_id: str
    name: str | None = None
    name_en: str | None = None
    kind: Literal["dormitory", "library", "sports", "academic", "student_life", "other"] = "other"
    lat: float
    lon: float


class Campus(BaseModel):
    osm_type: str | None = None
    osm_id: int | None = None
    osm_url: str | None = None
    polygon: list[list[float]] | None = None
    bbox: list[float]
    mode: Literal["polygon", "radius"] = "radius"
    radius_m: float = 500.0
    buildings: list[Building] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class PhotoCandidate(BaseModel):
    """A photo URL discovered by a source, before download."""
    url: str
    page_url: str
    source: str
    title: str | None = None
    text: str = ""          # any text we can match aliases against (caption, alt, page title, path)
    author: str | None = None
    license: str | None = None
    date: str | None = None
    date_source: str | None = None
    lat: float | None = None
    lon: float | None = None
    width: int | None = None
    height: int | None = None
    is_city: bool = False
    collector: str | None = None   # who found it (e.g. "node-commons"); informational, does not change trust


class Signal(BaseModel):
    key: str
    label: str
    weight: float
    value: str | None = None


class AiVerdict(BaseModel):
    """What the vision model said about one photo (see pipeline/ai_inspector.py)."""
    place: Literal["this_university", "city", "other_place", "unknown", "not_photo"]
    rel: int = Field(ge=0, le=3)          # how sure the photo shows this university (or, for place=city, this city)
    cat: str                              # one of CATEGORIES or "none"
    q: int = Field(ge=0, le=3)            # usefulness for a prospective student
    flags: list[str] = Field(default_factory=list)
    era: str = "unknown"                  # 2020s | 2010s | 2000s | older | unknown
    why: str = ""
    model: str = ""


class PhotoRef(BaseModel):
    id: str
    thumb: str
    source: str
    page_url: str
    similarity: float | None = None


class Photo(BaseModel):
    id: str
    url: str
    page_url: str
    thumb: str
    width: int
    height: int
    source: str
    source_label: str
    sources: list[str] = Field(default_factory=list)
    sources_count: int = 1
    title: str | None = None
    author: str | None = None
    license: str | None = None
    date: str | None = None
    date_source: str | None = None
    year: int | None = None
    outdated: bool = False
    lat: float | None = None
    lon: float | None = None
    geo_distance_m: float | None = None
    geo_inside: bool | None = None
    building: str | None = None
    building_kind: str | None = None
    category: str = "campus"
    secondary: str | None = None
    category_scores: dict[str, float] = Field(default_factory=dict)
    junk_score: float = 0.0
    junk_soft: bool = False   # text-heavy but from a trusted source (banner on a real building): penalised, not rejected
    stock_hint: bool = False  # URL/caption mentions a stock site (unsplash, shutterstock…)
    confidence: float = 0.0
    level: Literal["verified", "likely", "unverified"] = "unverified"
    signals: list[Signal] = Field(default_factory=list)
    phash: str | None = None
    dhash: str | None = None   # 64-bit difference hash, hex — same algorithm as the Node/Sharp part
    sha1: str | None = None    # SHA-1 of the downloaded bytes — exact-copy detection across stacks
    similar: list[PhotoRef] = Field(default_factory=list)
    is_brochure: bool = False
    rejected: bool = False
    reject_reason: str | None = None
    preliminary: bool = False
    ai: AiVerdict | None = None          # vision-model verdict; None when no model looked at the photo
    quality: int | None = None           # 0-3 usefulness (from the verdict)
    date_estimate: str | None = None     # "2010s" etc. when no real date is known; always shown as an estimate
    ref_similarity: float | None = None  # CLIP cosine to the reference photo of the university
    featured: bool = False               # picked by the curator for the compact per-category set


class Stage(BaseModel):
    key: str
    label: str
    status: Literal["pending", "running", "done", "skipped", "error"] = "pending"
    ms: int | None = None
    detail: str | None = None
    count: int | None = None


class CategoryStats(BaseModel):
    verified: int = 0
    likely: int = 0
    rejected: int = 0
    sources_checked: list[str] = Field(default_factory=list)
    coverage: Literal["strong", "medium", "weak", "none"] = "none"


class Sentence(BaseModel):
    text: str
    sources: list[int] = Field(default_factory=list)


class DescriptionSource(BaseModel):
    id: int
    label: str
    url: str


class Description(BaseModel):
    mode: Literal["llm", "template"] = "template"
    sentences: list[Sentence] = Field(default_factory=list)
    sources: list[DescriptionSource] = Field(default_factory=list)
    note: str | None = None


class Context(BaseModel):
    distance_km: float | None = None
    center_name: str | None = None
    transport_stops: int | None = None
    climate: dict[str, float] | None = None
    climate_note: str | None = None
    climate_url: str | None = None


class Profile(BaseModel):
    university: University
    campus: Campus | None = None
    photos: list[Photo] = Field(default_factory=list)
    rejected: list[Photo] = Field(default_factory=list)
    categories: dict[str, CategoryStats] = Field(default_factory=dict)
    coverage: dict[str, str] = Field(default_factory=dict)
    description: Description | None = None
    context: Context | None = None
    walk: list[Photo] = Field(default_factory=list)
    timeline: dict[str, int] = Field(default_factory=dict)
    stages: list[Stage] = Field(default_factory=list)
    sources_status: dict[str, dict] = Field(default_factory=dict)
    log: list[str] = Field(default_factory=list)
    generated_at: str
    elapsed_ms: int = 0
    partial: bool = False
    cached: bool = False
    reference: dict | None = None        # {url, page_url, source} of the trusted reference photo shown to the inspector
    inspector: dict | None = None        # {model, photos, calls, tokens_in, tokens_out, ms, errors}
    version: str = "0.2"
