"""The same picture found for two universities in two different cities is a photo of neither.

A broad image search pulls in exactly this: a stock shot of "a university dormitory" that a dozen admission portals
reuse, a news illustration, a photo of another campus that a blog captioned wrongly. Each of them can look perfectly
plausible next to the reference photo. What gives them away is that CampusLens has already seen the same pixels
(perceptual hash, Hamming distance <= 6) in the profile of a university somewhere else.

Only search and crowd finds are checked. A photo from the university's own site or accounts stays trusted even if
another profile holds a copy - that copy is the other profile's mistake, not this one's. Universities within 30 km
of each other share streets, landmarks and sometimes buildings, so they are never compared with each other (a
distance, not a city name: Wikidata files Almaty universities under "Алма-Ата" and under its districts).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import numpy as np

from ..config import settings

MAX_DISTANCE = 6
SAME_CITY_KM = 30.0
TTL_S = 600.0

_state: dict = {"at": 0.0, "hashes": np.zeros(0, dtype=np.uint64), "owner": [], "names": {}, "where": {}}
_lock = asyncio.Lock()


def _popcount(x: np.ndarray) -> np.ndarray:
    x = x - ((x >> np.uint64(1)) & np.uint64(0x5555555555555555))
    x = (x & np.uint64(0x3333333333333333)) + ((x >> np.uint64(2)) & np.uint64(0x3333333333333333))
    x = (x + (x >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return (x * np.uint64(0x0101010101010101)) >> np.uint64(56)


def _build() -> dict:
    """Every kept, non-city photo of every cached profile (runs in a thread: it parses all profiles once)."""
    db = sqlite3.connect(settings.data_dir / "campuslens.sqlite3")
    hashes: list[int] = []
    owner: list[str] = []
    names: dict[str, str] = {}
    where: dict[str, tuple[float, float]] = {}
    try:
        for qid, js in db.execute("SELECT qid, json FROM profiles"):
            try:
                p = json.loads(js)
            except ValueError:
                continue
            u = p.get("university") or {}
            names[qid] = u.get("name") or qid
            lat, lon = u.get("lat") or u.get("city_lat"), u.get("lon") or u.get("city_lon")
            if lat is not None and lon is not None:
                where[qid] = (lat, lon)
            for ph in p.get("photos") or []:
                if ph.get("category") == "city" or not ph.get("phash"):
                    continue
                try:
                    hashes.append(int(ph["phash"], 16))
                except ValueError:
                    continue
                owner.append(qid)
    finally:
        db.close()
    return {"at": time.monotonic(), "hashes": np.array(hashes, dtype=np.uint64), "owner": owner,
            "names": names, "where": where}


async def refresh() -> None:
    async with _lock:
        if time.monotonic() - _state["at"] > TTL_S:
            _state.update(await asyncio.to_thread(_build))


def elsewhere(phash: str | None, qid: str, lat: float | None, lon: float | None) -> tuple[str, int] | None:
    """(name of a university more than 30 km away holding the same picture, hash distance) or None."""
    from ..geo import haversine_km
    hs = _state["hashes"]
    if not phash or not len(hs):
        return None
    try:
        h = np.uint64(int(phash, 16))
    except ValueError:
        return None
    d = _popcount(np.bitwise_xor(hs, h))
    best: tuple[str, int] | None = None
    for i in np.nonzero(d <= MAX_DISTANCE)[0]:
        other = _state["owner"][i]
        there = _state["where"].get(other)
        if other == qid or lat is None or there is None or haversine_km(lat, lon, *there) <= SAME_CITY_KM:
            continue
        if best is None or int(d[i]) < best[1]:
            best = (_state["names"].get(other, other), int(d[i]))
    return best
