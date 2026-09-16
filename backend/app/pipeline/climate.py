"""Climate pack from Open-Meteo: last full year of ERA5 daily data → months, seasons, comfort index, wind rose,
plus current weather and a 5-day forecast. Cached for 30 days (forecast part is refreshed hourly by the caller)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from .. import http

log = logging.getLogger("campuslens.climate")

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
DAILY = ["temperature_2m_mean", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_mean",
         "relative_humidity_2m_mean", "precipitation_sum", "snowfall_sum", "sunshine_duration", "cloud_cover_mean",
         "wind_speed_10m_max", "wind_direction_10m_dominant"]
SEASONS = {"winter": [12, 1, 2], "spring": [3, 4, 5], "summer": [6, 7, 8], "autumn": [9, 10, 11]}
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
    if 10 <= t <= 25 and (r["wind"] or 0) < 40:
        return "comfortable"
    return "cool"


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
    arch = await http.get_json(ARCHIVE, params={
        "latitude": lat, "longitude": lon, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
        "daily": ",".join(DAILY), "timezone": "auto"}, timeout=8.0)
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
    seasons = {k: {"label": SEASON_RU[k], **_agg([r for r in rows if r["m"] in ms])} for k, ms in SEASONS.items()}
    comfort = {"comfortable": 0, "cool": 0, "freezing": 0, "hot": 0, "rainy": 0}
    for r in rows:
        k = _comfort(r)
        if k in comfort:
            comfort[k] += 1
    rose = []
    for i, name in enumerate(DIRS):
        sel = [r for r in rows if r["wdir"] is not None and int(((r["wdir"] + 22.5) % 360) // 45) == i]
        rose.append({"dir": name, "share": round(len(sel) / max(1, len(rows)), 3), "speed": _mean([(r["wind"] or 0) / 3.6 for r in sel]) or 0})
    annual = _agg(rows)
    annual["sun_hours"] = round(sum((r["sun"] or 0) for r in rows) / 3600)
    pack = {
        "year": year, "timezone": arch.get("timezone"),
        "source": {"label": "Open-Meteo, ERA5", "url": f"https://open-meteo.com/en/docs/historical-weather-api#latitude={lat}&longitude={lon}"},
        "annual": annual, "months": months, "seasons": seasons, "comfort": comfort, "wind_rose": rose,
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
