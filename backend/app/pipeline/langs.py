"""The language a university's own students write in, and how they say "campus" in it.

Social search has to be run in that language: students of TU München post "Studentenleben", students in Tokyo post
"大学生活", nobody at the University of Toronto searches for "Университет Торонто". The language comes from the
university's country, the name in it from Wikidata, the words from the table below - so it works for any university
in any of the countries we index, and falls back to English for the rest.
"""
from __future__ import annotations

import json
from functools import lru_cache

from ..config import settings
from ..models import University

# the language people actually search in; English where it is the everyday language of campus social media
LANG_BY_ISO = {
    "US": "en", "GB": "en", "CA": "en", "AU": "en", "IE": "en", "IN": "en", "SG": "en", "AE": "en",
    "RU": "ru", "KZ": "ru", "BY": "ru", "KG": "ru", "TJ": "ru", "TM": "ru", "UZ": "ru",
    "UA": "uk", "DE": "de", "AT": "de", "CH": "de", "FR": "fr", "BE": "fr", "IT": "it", "ES": "es", "PT": "pt",
    "PL": "pl", "NL": "nl", "CZ": "cs", "HU": "hu", "SE": "sv", "FI": "fi", "DK": "da", "NO": "nb", "BG": "bg",
    "LV": "lv", "LT": "lt", "EE": "et", "TR": "tr", "AZ": "az", "GE": "ka", "EG": "ar", "MY": "ms",
    "CN": "zh", "JP": "ja", "KR": "ko",
}

# how students say it, per language: every place an applicant wants to see, plus "atmosphere"
# (and "campus tour", which only English uses). A query is "<name> <word>"; the inspector decides the category.
CONCEPTS: dict[str, dict[str, str]] = {
    "en": {
        "campus": "campus", "student_life": "student life", "dormitory": "dorm", "atmosphere": "atmosphere",
        "campus_tour": "campus tour", "classroom": "lecture hall", "assembly_hall": "assembly hall",
        "library": "library", "lab": "laboratory", "sports": "gym", "canteen": "canteen",
        "aerial": "drone",
    },
    "ru": {
        "campus": "кампус", "student_life": "студенческая жизнь", "dormitory": "общежитие",
        "atmosphere": "атмосфера", "classroom": "аудитория", "assembly_hall": "актовый зал", "library": "библиотека",
        "lab": "лаборатория", "sports": "спортзал", "canteen": "столовая",
        "aerial": "с высоты",
    },
    "uk": {
        "campus": "кампус", "student_life": "студентське життя", "dormitory": "гуртожиток",
        "atmosphere": "атмосфера", "classroom": "аудиторія", "assembly_hall": "актова зала", "library": "бібліотека",
        "lab": "лабораторія", "sports": "спортзал", "canteen": "їдальня",
        "aerial": "з висоти",
    },
    "kk": {
        "campus": "кампус", "student_life": "студенттік өмір", "dormitory": "жатақхана", "atmosphere": "атмосфера",
        "classroom": "аудитория", "assembly_hall": "мәжіліс залы", "library": "кітапхана", "lab": "зертхана",
        "sports": "спорт залы", "canteen": "асхана",
        "aerial": "дрон",
    },
    "de": {
        "campus": "Campus", "student_life": "Studentenleben", "dormitory": "Wohnheim", "atmosphere": "Atmosphäre",
        "classroom": "Hörsaal", "assembly_hall": "Aula", "library": "Bibliothek", "lab": "Labor",
        "sports": "Sporthalle", "canteen": "Mensa",
        "aerial": "Luftaufnahme",
    },
    "fr": {
        "campus": "campus", "student_life": "vie étudiante", "dormitory": "résidence universitaire",
        "atmosphere": "ambiance", "classroom": "amphithéâtre", "assembly_hall": "grand amphithéâtre",
        "library": "bibliothèque universitaire", "lab": "laboratoire", "sports": "gymnase",
        "canteen": "restaurant universitaire",
        "aerial": "vue aérienne",
    },
    "es": {
        "campus": "campus", "student_life": "vida universitaria", "dormitory": "residencia universitaria",
        "atmosphere": "ambiente", "classroom": "aula", "assembly_hall": "salón de actos", "library": "biblioteca",
        "lab": "laboratorio", "sports": "gimnasio", "canteen": "cafetería",
        "aerial": "vista aérea",
    },
    "it": {
        "campus": "campus", "student_life": "vita universitaria", "dormitory": "residenza universitaria",
        "atmosphere": "atmosfera", "classroom": "aula", "assembly_hall": "aula magna", "library": "biblioteca",
        "lab": "laboratorio", "sports": "palestra", "canteen": "mensa",
        "aerial": "vista aerea",
    },
    "pt": {
        "campus": "campus", "student_life": "vida universitária", "dormitory": "residência universitária",
        "atmosphere": "ambiente", "classroom": "sala de aula", "assembly_hall": "auditório", "library": "biblioteca",
        "lab": "laboratório", "sports": "ginásio", "canteen": "cantina",
        "aerial": "vista aérea",
    },
    "pl": {
        "campus": "kampus", "student_life": "życie studenckie", "dormitory": "akademik", "atmosphere": "atmosfera",
        "classroom": "sala wykładowa", "assembly_hall": "aula", "library": "biblioteka", "lab": "laboratorium",
        "sports": "hala sportowa", "canteen": "stołówka",
        "aerial": "z lotu ptaka",
    },
    "nl": {
        "campus": "campus", "student_life": "studentenleven", "dormitory": "studentenhuis", "atmosphere": "sfeer",
        "classroom": "collegezaal", "assembly_hall": "aula", "library": "bibliotheek", "lab": "laboratorium",
        "sports": "sporthal", "canteen": "mensa",
        "aerial": "luchtfoto",
    },
    "cs": {
        "campus": "kampus", "student_life": "studentský život", "dormitory": "kolej", "atmosphere": "atmosféra",
        "classroom": "posluchárna", "assembly_hall": "aula", "library": "knihovna", "lab": "laboratoř",
        "sports": "tělocvična", "canteen": "menza",
        "aerial": "z dronu",
    },
    "hu": {
        "campus": "kampusz", "student_life": "egyetemi élet", "dormitory": "kollégium", "atmosphere": "hangulat",
        "classroom": "előadóterem", "assembly_hall": "díszterem", "library": "könyvtár", "lab": "labor",
        "sports": "tornaterem", "canteen": "menza",
        "aerial": "drónfelvétel",
    },
    "sv": {
        "campus": "campus", "student_life": "studentliv", "dormitory": "studentboende", "atmosphere": "stämning",
        "classroom": "föreläsningssal", "assembly_hall": "aula", "library": "bibliotek", "lab": "laboratorium",
        "sports": "idrottshall", "canteen": "studentrestaurang",
        "aerial": "drönarbild",
    },
    "fi": {
        "campus": "kampus", "student_life": "opiskelijaelämä", "dormitory": "opiskelija-asunto",
        "atmosphere": "tunnelma", "classroom": "luentosali", "assembly_hall": "juhlasali", "library": "kirjasto",
        "lab": "laboratorio", "sports": "liikuntasali", "canteen": "opiskelijaravintola",
        "aerial": "ilmakuva",
    },
    "da": {
        "campus": "campus", "student_life": "studieliv", "dormitory": "kollegium", "atmosphere": "stemning",
        "classroom": "auditorium", "assembly_hall": "festsal", "library": "bibliotek", "lab": "laboratorium",
        "sports": "idrætshal", "canteen": "kantine",
        "aerial": "luftfoto",
    },
    "nb": {
        "campus": "campus", "student_life": "studentliv", "dormitory": "studenthybel", "atmosphere": "stemning",
        "classroom": "auditorium", "assembly_hall": "aula", "library": "bibliotek", "lab": "laboratorium",
        "sports": "idrettshall", "canteen": "kantine",
        "aerial": "dronebilde",
    },
    "bg": {
        "campus": "кампус", "student_life": "студентски живот", "dormitory": "общежитие", "atmosphere": "атмосфера",
        "classroom": "аудитория", "assembly_hall": "актова зала", "library": "библиотека", "lab": "лаборатория",
        "sports": "физкултурен салон", "canteen": "студентски стол",
        "aerial": "от въздуха",
    },
    "lv": {
        "campus": "kampuss", "student_life": "studentu dzīve", "dormitory": "kopmītnes", "atmosphere": "atmosfēra",
        "classroom": "auditorija", "assembly_hall": "aula", "library": "bibliotēka", "lab": "laboratorija",
        "sports": "sporta zāle", "canteen": "ēdnīca",
        "aerial": "no putna lidojuma",
    },
    "lt": {
        "campus": "miestelis", "student_life": "studentiškas gyvenimas", "dormitory": "bendrabutis",
        "atmosphere": "atmosfera", "classroom": "auditorija", "assembly_hall": "aktų salė", "library": "biblioteka",
        "lab": "laboratorija", "sports": "sporto salė", "canteen": "valgykla",
        "aerial": "iš paukščio skrydžio",
    },
    "et": {
        "campus": "ülikoolilinnak", "student_life": "tudengielu", "dormitory": "ühiselamu", "atmosphere": "õhkkond",
        "classroom": "auditoorium", "assembly_hall": "aula", "library": "raamatukogu", "lab": "labor",
        "sports": "spordisaal", "canteen": "söökla",
        "aerial": "droonifoto",
    },
    "tr": {
        "campus": "kampüs", "student_life": "öğrenci hayatı", "dormitory": "yurt", "atmosphere": "atmosfer",
        "classroom": "amfi", "assembly_hall": "konferans salonu", "library": "kütüphane", "lab": "laboratuvar",
        "sports": "spor salonu", "canteen": "yemekhane",
        "aerial": "drone çekimi",
    },
    "az": {
        "campus": "kampus", "student_life": "tələbə həyatı", "dormitory": "yataqxana", "atmosphere": "atmosfer",
        "classroom": "auditoriya", "assembly_hall": "akt zalı", "library": "kitabxana", "lab": "laboratoriya",
        "sports": "idman zalı", "canteen": "yeməkxana",
        "aerial": "dron çəkilişi",
    },
    "ka": {
        "campus": "კამპუსი", "student_life": "სტუდენტური ცხოვრება", "dormitory": "საერთო საცხოვრებელი",
        "atmosphere": "ატმოსფერო", "classroom": "აუდიტორია", "assembly_hall": "სააქტო დარბაზი",
        "library": "ბიბლიოთეკა", "lab": "ლაბორატორია", "sports": "სპორტული დარბაზი", "canteen": "სასადილო",
        "aerial": "დრონით",
    },
    "ar": {
        "campus": "الحرم الجامعي", "student_life": "الحياة الجامعية", "dormitory": "السكن الجامعي",
        "atmosphere": "أجواء الجامعة", "classroom": "قاعة المحاضرات", "assembly_hall": "قاعة الاحتفالات",
        "library": "المكتبة", "lab": "المختبر", "sports": "الصالة الرياضية", "canteen": "الكافتيريا",
        "aerial": "تصوير جوي",
    },
    "ms": {
        "campus": "kampus", "student_life": "kehidupan pelajar", "dormitory": "asrama", "atmosphere": "suasana",
        "classroom": "dewan kuliah", "assembly_hall": "dewan besar", "library": "perpustakaan", "lab": "makmal",
        "sports": "gimnasium", "canteen": "kafeteria",
        "aerial": "pemandangan udara",
    },
    "zh": {
        "campus": "校园", "student_life": "大学生活", "dormitory": "宿舍", "atmosphere": "校园风景", "classroom": "教室",
        "assembly_hall": "礼堂", "library": "图书馆", "lab": "实验室", "sports": "体育馆", "canteen": "食堂",
        "aerial": "航拍",
    },
    "ja": {
        "campus": "キャンパス", "student_life": "大学生活", "dormitory": "学生寮", "atmosphere": "キャンパスの雰囲気", "classroom": "講義室",
        "assembly_hall": "講堂", "library": "図書館", "lab": "研究室", "sports": "体育館", "canteen": "学食",
        "aerial": "空撮",
    },
    "ko": {
        "campus": "캠퍼스", "student_life": "대학 생활", "dormitory": "기숙사", "atmosphere": "캠퍼스 분위기", "classroom": "강의실",
        "assembly_hall": "강당", "library": "도서관", "lab": "연구실", "sports": "체육관", "canteen": "학생식당",
        "aerial": "항공샷",
    },
}
# the fast pass asks the two queries that pay off most: what the place looks like, and where students live
FAST_EN = ("campus", "student_life")
FAST_LOCAL = ("campus", "dormitory")

# every language we may need a university's name in (one Wikidata request fetches them all)
WIKIDATA_LANGS = sorted({"ru", "en", "kk", *LANG_BY_ISO.values()})


@lru_cache(maxsize=1)
def _countries() -> list[dict]:
    try:
        return json.loads((settings.data_dir.parents[1] / "frontend" / "public" / "countries.json")
                          .read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def iso_of_country_qid(qid: str | None) -> str | None:
    return next((c["iso"] for c in _countries() if c["qid"] == qid), None) if qid else None


def iso_of(uni: University) -> str | None:
    """Country code from the index row, else from the country label (web-found universities have only that)."""
    from .resolve import index, normalize
    row = index.by_id.get(uni.qid)
    if row and row.get("country"):
        return iso_of_country_qid(row["country"])
    name = normalize(uni.country or "")
    return next((c["iso"] for c in _countries() for k in ("ru", "en", "kk") if c.get(k) and normalize(c[k]) == name),
                None) if name else None


def local_lang(uni: University) -> str:
    return LANG_BY_ISO.get(iso_of(uni) or "", "en")
