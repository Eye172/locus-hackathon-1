"""Climate pack from Open-Meteo: last full year of ERA5 daily data → months, seasons, comfort index, wind rose,
plus current weather and a 5-day forecast. Cached for 30 days (forecast part is refreshed hourly by the caller).
story() turns the pack into a short text for a student: how the year feels, what to wear, when to visit."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone

from .. import http
from ..config import settings

log = logging.getLogger("campuslens.climate")

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DAILY = ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_mean",
         "relative_humidity_2m_mean", "precipitation_sum", "snowfall_sum", "sunshine_duration", "cloud_cover_mean",
         "wind_speed_10m_max", "wind_direction_10m_dominant"]
SEASONS = {"winter": [12, 1, 2], "spring": [3, 4, 5], "summer": [6, 7, 8], "autumn": [9, 10, 11]}
# south of the equator the calendar seasons are the other way round: winter is June-August
SEASONS_SOUTH = {"winter": [6, 7, 8], "spring": [9, 10, 11], "summer": [12, 1, 2], "autumn": [3, 4, 5]}
PACK_VERSION = 3   # v2: mean daily highs/lows, hemisphere-aware seasons, a class per day; v3: "warm" (+25…+30)
SEASON_RU = {"winter": "Зима", "spring": "Весна", "summer": "Лето", "autumn": "Осень"}
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
MONTHS_RU = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _agg(rows: list[dict]) -> dict:
    return {
        "t_mean": _mean([r["t"] for r in rows]), "t_max": max((r["tmax"] for r in rows if r["tmax"] is not None), default=None),
        "t_min": min((r["tmin"] for r in rows if r["tmin"] is not None), default=None),
        "t_hi": _mean([r["tmax"] for r in rows]), "t_lo": _mean([r["tmin"] for r in rows]),
        "feels": _mean([r["feels"] for r in rows]), "humidity": _mean([r["hum"] for r in rows]),
        "precip_mm": round(sum(r["precip"] or 0 for r in rows), 0), "precip_days": sum(1 for r in rows if (r["precip"] or 0) >= 1.0),
        "snow_days": sum(1 for r in rows if (r["snow"] or 0) >= 1.0),
        "sun_h_day": _mean([(r["sun"] or 0) / 3600 for r in rows]), "wind_ms": _mean([(r["wind"] or 0) / 3.6 for r in rows]),
        "cloud": _mean([r["cloud"] for r in rows]),
    }


def _comfort(r: dict) -> str:
    t = r["feels"] if r["feels"] is not None else r["t"]
    if t is None:
        return "unknown"
    if (r["precip"] or 0) >= 3:
        return "rainy"
    if t <= -15:
        return "freezing"
    if t >= 30:
        return "hot"
    if t > 25:
        return "warm"
    if t >= 10 and (r["wind"] or 0) < 40:
        return "comfortable"
    return "cool"   # below +10, or a comfortable temperature under a strong wind


def feels_text(pack: dict, city: str | None) -> str:
    s = pack["seasons"]
    w, su = s["winter"], s["summer"]
    c = pack["comfort"]
    parts = []
    if w["t_mean"] is not None:
        kind = "суровая" if w["t_mean"] <= -10 else "холодная" if w["t_mean"] <= -2 else "мягкая"
        parts.append(f"Зима {kind}: в среднем {w['t_mean']:+.0f} °C, ощущается как {w['feels']:+.0f} °C, снежных дней около {w['snow_days']}.")
    if su["t_mean"] is not None:
        kind = "жаркое" if su["t_max"] and su["t_max"] >= 32 else "тёплое"
        parts.append(f"Лето {kind}: в среднем {su['t_mean']:+.0f} °C, максимум до {su['t_max']:+.0f} °C, дождливых дней {su['precip_days']}.")
    parts.append(f"Комфортных дней в году {c['comfortable']}, морозных {c['freezing']}, жарких {c['hot']}; солнца в среднем {pack['annual']['sun_h_day']} ч в день.")
    place = f" для {city}" if city else ""
    return " ".join(parts) + f" Данные{place}: Open-Meteo, реанализ ERA5, {pack['year']} год."


async def build(lat: float, lon: float, city: str | None = None) -> dict:
    year = date.today().year - 1
    params = {"latitude": lat, "longitude": lon, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
              "daily": ",".join(DAILY), "timezone": "auto"}
    # the archive answers a place it has not served lately in up to ~30 s (then in < 1 s): wait, and retry once.
    # A year of 11 daily variables costs ~29 of its free calls (600 a minute, 5000 an hour): after a 429 wait a bit
    try:
        arch = await http.get_json(ARCHIVE, params=params, timeout=35.0)
    except Exception as e:  # noqa: BLE001
        log.info("climate archive retry after %r", e)
        if "429" in str(e):
            await asyncio.sleep(10)
        arch = await http.get_json(ARCHIVE, params=params, timeout=35.0)
    d = arch["daily"]
    rows = []
    for i, day in enumerate(d["time"]):
        rows.append({"day": day, "m": int(day[5:7]), "t": d["temperature_2m_mean"][i], "tmax": d["temperature_2m_max"][i],
                     "tmin": d["temperature_2m_min"][i], "feels": d["apparent_temperature_mean"][i],
                     "hum": d["relative_humidity_2m_mean"][i], "precip": d["precipitation_sum"][i], "snow": d["snowfall_sum"][i],
                     "sun": d["sunshine_duration"][i], "cloud": d["cloud_cover_mean"][i], "wind": d["wind_speed_10m_max"][i],
                     "wdir": d["wind_direction_10m_dominant"][i]})
    months = []
    for m in range(1, 13):
        mr = [r for r in rows if r["m"] == m]
        if mr:
            months.append({"m": m, "label": MONTHS_RU[m - 1], **_agg(mr)})
    season_months = SEASONS_SOUTH if lat < 0 else SEASONS
    seasons = {k: {"label": SEASON_RU[k], "months": ms, **_agg([r for r in rows if r["m"] in ms])}
               for k, ms in season_months.items()}
    comfort = {"comfortable": 0, "cool": 0, "freezing": 0, "warm": 0, "hot": 0, "rainy": 0}
    letters = {"comfortable": "c", "cool": "k", "freezing": "f", "warm": "w", "hot": "h", "rainy": "r"}
    day_classes = []
    for r in rows:
        k = _comfort(r)
        if k in comfort:
            comfort[k] += 1
        day_classes.append(letters.get(k, "-"))
    rose = []
    for i, name in enumerate(DIRS):
        sel = [r for r in rows if r["wdir"] is not None and int(((r["wdir"] + 22.5) % 360) // 45) == i]
        rose.append({"dir": name, "share": round(len(sel) / max(1, len(rows)), 3), "speed": _mean([(r["wind"] or 0) / 3.6 for r in sel]) or 0})
    annual = _agg(rows)
    annual["sun_hours"] = round(sum((r["sun"] or 0) for r in rows) / 3600)
    pack = {
        "year": year, "timezone": arch.get("timezone"),
        "source": {"label": "Open-Meteo, ERA5", "url": f"https://open-meteo.com/en/docs/historical-weather-api#latitude={lat}&longitude={lon}"},
        "v": PACK_VERSION, "hemisphere": "south" if lat < 0 else "north",
        "annual": annual, "months": months, "seasons": seasons, "comfort": comfort, "wind_rose": rose,
        # one entry per day of the year: comfort class letter, feels-like °C, precipitation mm
        "days": {"start": rows[0]["day"] if rows else f"{year}-01-01", "cls": "".join(day_classes),
                 "feels": [None if r["feels"] is None else round(r["feels"]) for r in rows],
                 "precip": [round(r["precip"] or 0, 1) for r in rows]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    pack["feels_text"] = {"mode": "template", "text": feels_text(pack, city)}
    return pack


async def now(lat: float, lon: float) -> dict | None:
    try:
        d = await http.get_json(FORECAST, params={
            "latitude": lat, "longitude": lon, "forecast_days": 6, "timezone": "auto",
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min"}, timeout=5.0)
        c = d["current"]
        f = d["daily"]
        return {"now": {"t": c["temperature_2m"], "feels": c["apparent_temperature"], "humidity": c["relative_humidity_2m"],
                        "wind_ms": round(c["wind_speed_10m"] / 3.6, 1), "code": c["weather_code"], "time": c["time"]},
                "forecast": [{"date": f["time"][i], "t_max": f["temperature_2m_max"][i], "t_min": f["temperature_2m_min"][i], "code": f["weather_code"][i]}
                             for i in range(min(6, len(f["time"])))]}
    except Exception as e:  # noqa: BLE001
        log.info("forecast failed: %s", e)
        return None


STORY_VERSION = 3
LANG_NAME = {"ru": "русском", "en": "английском", "kk": "казахском"}
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STORY_PROMPT = """Ты — редактор путеводителя для абитуриентов. Напиши раздел «Как ощущается климат» для профиля вуза.
Вуз: {name}. Город: {city}. {hemisphere}
Ниже — реальные данные Open-Meteo (реанализ ERA5) за {year} год. Других данных о погоде у тебя нет.

По месяцам (днём — средний дневной максимум, ночью — средний ночной минимум, ощущается — средняя ощущаемая температура за сутки, °C):
{table}

За год: комфортных дней {comfortable} (ощущается +10…+25 °C, без сильного ветра и дождя), прохладных (ниже +10 °C или сильный ветер) {cool}, морозных (ощущается ниже −15 °C) {freezing}, тёплых (+25…+30 °C) {warm}, жарких (выше +30 °C) {hot}, дождливых (осадки от 3 мм) {rainy}; солнца {sun_hours} ч за год; чаще всего ветер {wind}.

Правила:
- Пиши на {lang_name} языке, спокойно, конкретно и живо — как старшекурсник, который здесь учится. Без восклицаний, эмодзи, канцелярита и пустых слов вроде «разнообразный климат» или «каждый найдёт что-то своё».
- Каждое утверждение опирай на цифры из таблицы; числа бери только оттуда (округлять можно). Не сравнивай с другими городами, не упоминай события, праздники и достопримечательности, не выдумывай местные особенности.
- Нужны бытовые примеры из жизни студента: дорога на пары утром, что надеть, когда можно сидеть на траве у корпуса, когда рано темнеет (если солнца мало), когда нужен зонт, как ощущается ветер, когда приходится прятаться от жары.
- Одежду подбирай по ощущаемой температуре, без преувеличений: ниже −15 — пуховик, шапка, варежки, зимние ботинки; −15…−5 — тёплая зимняя куртка и шапка; −5…+5 — утеплённая куртка; +5…+12 — демисезонная куртка; +12…+18 — лёгкая куртка или худи; +18…+25 — футболка, вечером кофта; выше +25 — лёгкая одежда, вода и кепка. Зонт — только если дождливых дней в месяц 8 и больше.
- Не называй тип климата (континентальный, субтропический и т. п.). Температуру пиши как +25 °C или −4 °C.
- Учебный год: {academic}.

Верни JSON:
lead — 1–2 предложения: главное, что нужно знать о погоде здесь;
story — 3–5 предложений: как погода меняется за учебный год по месяцам, с цифрами и бытовыми примерами;
seasons — по одной короткой фразе (до 16 слов) для winter, spring, summer, autumn: какой у сезона характер и что именно надеть (обязательно назови одежду); не начинай с цифр и не повторяй «ощущается от … до …» — цифры сезона уже показаны рядом;
packing — 3–6 коротких пунктов (2–5 слов): что привезти с собой, без брендов;
best — одно предложение: в какие месяцы лучше приехать посмотреть кампус и почему."""
STORY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lead": {"type": "STRING"}, "story": {"type": "STRING"},
        "seasons": {"type": "OBJECT", "properties": {k: {"type": "STRING"} for k in SEASONS},
                    "required": list(SEASONS)},
        "packing": {"type": "ARRAY", "items": {"type": "STRING"}},
        "best": {"type": "STRING"},
    },
    "required": ["lead", "story", "seasons", "packing", "best"],
}


def _n(v: float | None) -> str:
    if v is None:
        return "—"
    r = round(v)
    return "0" if r == 0 else f"{r:+d}".replace("-", "−")


def _story_prompt(pack: dict, name: str, city: str | None, lang: str) -> str:
    rows = []
    for m in pack["months"]:
        rows.append(f"{MONTHS_EN[m['m'] - 1]}: днём {_n(m.get('t_hi'))}, ночью {_n(m.get('t_lo'))}, ощущается {_n(m['feels'])}; "
                    f"осадки {m['precip_days']} дн. ({m['precip_mm']:.0f} мм), снег {m['snow_days']} дн.; "
                    f"солнце {m['sun_h_day']} ч/день; ветер {m['wind_ms']} м/с; влажность {m['humidity']} %")
    rose = max(pack["wind_rose"], key=lambda r: r["share"])
    south = pack.get("hemisphere") == "south"
    return STORY_PROMPT.format(
        name=name, city=city or "—", year=pack["year"], table="\n".join(rows),
        hemisphere="Южное полушарие: зима здесь в июне–августе, лето — в декабре–феврале." if south else "",
        academic="примерно с марта по ноябрь" if south else "с сентября по июнь, летом каникулы",
        wind=f"{rose['dir']} ({round(rose['share'] * 100)} % дней, в среднем {rose['speed']} м/с)",
        lang_name=LANG_NAME.get(lang, "русском"), sun_hours=pack["annual"]["sun_hours"],
        **{"warm": 0, **pack["comfort"]})   # an older saved pack (served when a rebuild failed) has no "warm"


async def story(pack: dict, name: str, city: str | None, lang: str = "ru") -> dict:
    """How the climate feels, written for a student from the pack's own numbers. Falls back to the template."""
    fallback = {"mode": "template", "lead": pack["feels_text"]["text"], "story": "", "seasons": {}, "packing": [], "best": ""}
    if settings.active_llm() != "gemini":
        return fallback
    from .ai_inspector import gemini_post
    body = {"contents": [{"role": "user", "parts": [{"text": _story_prompt(pack, name, city, lang)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": STORY_SCHEMA,
                                 "temperature": 0.3}}
    try:
        j, model = await gemini_post(body, 25.0)
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
        out = json.loads(text)
    except Exception as e:  # noqa: BLE001
        log.warning("climate story failed for %s: %r", name, e)
        return fallback
    seasons = out.get("seasons") or {}
    return {"mode": "ai", "model": model, "lead": str(out.get("lead", "")).strip(), "story": str(out.get("story", "")).strip(),
            "seasons": {k: str(seasons.get(k, "")).strip() for k in SEASONS},
            "packing": [str(x).strip() for x in (out.get("packing") or []) if str(x).strip()][:6],
            "best": str(out.get("best", "")).strip()}
