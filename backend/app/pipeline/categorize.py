"""Category assignment: CLIP zero-shot × OSM building prior × multilingual keyword prior."""
from __future__ import annotations

import re

from ..models import CATEGORIES, Building, Photo
from ..geo import haversine_km
from .vision import JUNK_LABELS_RU

KEYWORD_PRIORS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"жатақхана|общежит|dorm|hostel|residence hall|студенттер үйі|obshezh|zhatak", re.I), "dormitory", 0.25),
    (re.compile(r"кітапхана|библиотек|library|reading room|читальн", re.I), "library", 0.25),
    (re.compile(r"зертхана|лаборатор|\blab\b|laborator|research cent", re.I), "lab", 0.25),
    (re.compile(r"спорт|стадион|sport|\bgym\b|stadium|бассейн|swimming|фитнес|fitness|арена|arena", re.I), "sports", 0.25),
    (re.compile(r"аудитор|lecture|classroom|дәріс|lecture hall|аудитория|учебн", re.I), "classroom", 0.20),
    (re.compile(r"кампус|campus|kampus|корпус|главное здание|main building|ректорат|rectorate", re.I), "campus", 0.15),
    (re.compile(r"universit|универси|университеті|institute|институт", re.I), "campus", 0.08),
    (re.compile(r"студенческ|студенттік|student life|фестивал|festival|концерт|concert|праздник|event|мероприят", re.I), "student_life", 0.20),
    (re.compile(r"skyline|панорама города|city view|downtown|центр города|проспект|avenue|street|улица", re.I), "city", 0.15),
]
BUILDING_TO_CATEGORY = {"dormitory": "dormitory", "library": "library", "sports": "sports",
                        "academic": "campus", "student_life": "student_life"}


def nearest_building(lat: float, lon: float, buildings: list[Building], max_m: float = 45.0) -> Building | None:
    best, best_d = None, max_m
    for b in buildings:
        d = haversine_km(lat, lon, b.lat, b.lon) * 1000
        if d < best_d:
            best, best_d = b, d
    return best


TRUSTED = {"commons_cat", "commons_depicts", "wikipedia", "official", "mapillary", "places"}
SOFT_JUNK = {"poster"}   # a banner on a real building looks like a poster to CLIP; trusted sources get a penalty, not a rejection
STOCK_RE = re.compile(r"unsplash|shutterstock|istock|pexels|freepik|depositphotos|stock[-_ ]?photo|gettyimages|adobe.?stock|dreamstime|123rf", re.I)


def categorize(p: Photo, clf: dict, text: str, is_city: bool, buildings: list[Building]) -> None:
    scores = dict(clf["categories"])
    junk_total = clf["junk_total"]
    best_cat = max(scores, key=scores.get)

    # stock-site marker in the URL or caption: a penalty (verify.py), not a rejection — genuine campus photos
    # do get uploaded to Unsplash; the other signals decide
    if STOCK_RE.search(f"{p.url} {p.page_url} {text or ''}"):
        p.stock_hint = True

    # junk decision: the junk classes dominate the picture
    if junk_total > 0.55 and junk_total > clf["categories_raw"].get(best_cat, 0):
        if clf["junk_top"] in SOFT_JUNK and p.source in TRUSTED and junk_total < 0.9:
            p.junk_soft = True
        else:
            p.rejected = True
            p.reject_reason = f"не фотография кампуса: {JUNK_LABELS_RU.get(clf['junk_top'], clf['junk_top'])} ({junk_total:.0%})"
    p.junk_score = round(junk_total, 3)

    # priors
    if p.lat is not None and p.lon is not None and buildings:
        b = nearest_building(p.lat, p.lon, buildings)
        if b:
            p.building = b.name_en or b.name
            p.building_kind = b.kind
            cat = BUILDING_TO_CATEGORY.get(b.kind)
            if cat:
                scores[cat] = scores.get(cat, 0) + 0.25
    for rx, cat, w in KEYWORD_PRIORS:
        if rx.search(text or ""):
            scores[cat] = scores.get(cat, 0) + w
    if is_city:
        scores["city"] = scores.get("city", 0) + 0.6

    total = sum(scores.values()) or 1.0
    scores = {c: round(v / total, 3) for c, v in scores.items()}
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    p.category = ranked[0][0]
    p.secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] >= 0.15 else None
    p.category_scores = {c: scores.get(c, 0.0) for c in CATEGORIES}
