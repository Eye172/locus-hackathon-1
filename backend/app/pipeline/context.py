"""Context around the campus: distance to the city centre, transport stops, climate."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from .. import http
from ..geo import haversine_km
from ..models import Campus, Context, University
from .sources import osm

log = logging.getLogger("campuslens.context")


async def climate(lat: float, lon: float) -> tuple[dict | None, str | None, str | None]:
    year = date.today().year - 1
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {"latitude": lat, "longitude": lon, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
              "daily": "temperature_2m_mean", "timezone": "auto"}
    try:
        d = await http.get_json(url, params=params, timeout=5.0)
        days = d["daily"]["time"]
        temps = d["daily"]["temperature_2m_mean"]
        by_month: dict[int, list[float]] = {}
        for day, t in zip(days, temps):
            if t is None:
                continue
            by_month.setdefault(int(day[5:7]), []).append(t)
        if not by_month:
            return None, None, None
        avg = {m: sum(v) / len(v) for m, v in by_month.items()}
        out = {"jan": round(avg.get(1, float("nan")), 1), "jul": round(avg.get(7, float("nan")), 1),
               "year": round(sum(temps_ := [t for t in temps if t is not None]) / len(temps_), 1)}
        note = f"Средние температуры за {year} год, реанализ ERA5 (Open-Meteo)"
        link = f"https://open-meteo.com/en/docs/historical-weather-api#latitude={lat}&longitude={lon}"
        return out, note, link
    except Exception as e:  # noqa: BLE001
        log.warning("climate failed: %s", e)
        return None, None, None


async def build(uni: University, campus: Campus | None) -> Context:
    ctx = Context()
    if uni.lat is None:
        return ctx
    if uni.city_lat is not None and uni.city_lon is not None:
        ctx.distance_km = round(haversine_km(uni.lat, uni.lon, uni.city_lat, uni.city_lon), 1)
        ctx.center_name = uni.city
    stops_task = asyncio.create_task(osm.transport_stops(uni.lat, uni.lon, 800))
    clim_task = asyncio.create_task(climate(uni.lat, uni.lon))
    try:
        ctx.transport_stops = await asyncio.wait_for(stops_task, timeout=7.0)
    except Exception:
        ctx.transport_stops = None
    try:
        ctx.climate, ctx.climate_note, ctx.climate_url = await asyncio.wait_for(clim_task, timeout=6.0)
    except Exception:
        pass
    return ctx
