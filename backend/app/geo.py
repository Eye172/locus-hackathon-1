"""Geometry helpers: distances, polygons, bounding boxes."""
from __future__ import annotations

import math
from typing import Iterable

from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def bbox_from_points(points: Iterable[tuple[float, float]], pad_m: float = 0.0) -> list[float]:
    """points are (lat, lon). Returns [minlat, minlon, maxlat, maxlon]."""
    lats, lons = zip(*points)
    minlat, maxlat, minlon, maxlon = min(lats), max(lats), min(lons), max(lons)
    if pad_m:
        dlat = pad_m / 111_320.0
        dlon = pad_m / (111_320.0 * max(math.cos(math.radians((minlat + maxlat) / 2)), 0.01))
        minlat, maxlat, minlon, maxlon = minlat - dlat, maxlat + dlat, minlon - dlon, maxlon + dlon
    return [minlat, minlon, maxlat, maxlon]


def bbox_around(lat: float, lon: float, radius_m: float) -> list[float]:
    return bbox_from_points([(lat, lon)], pad_m=radius_m)


def bbox_area_deg2(b: list[float]) -> float:
    return abs((b[2] - b[0]) * (b[3] - b[1]))


class CampusGeom:
    """Wraps a campus polygon (or a radius fallback) with fast containment checks."""

    def __init__(self, polygon: list[list[float]] | None, center: tuple[float, float], radius_m: float = 500.0):
        self.center = center
        self.radius_m = radius_m
        self.polygon = polygon
        self._shape: Polygon | MultiPolygon | None = None
        if polygon and len(polygon) >= 4:
            try:
                self._shape = Polygon([(lon, lat) for lat, lon in polygon])
                if not self._shape.is_valid:
                    self._shape = self._shape.buffer(0)
            except Exception:
                self._shape = None

    @property
    def mode(self) -> str:
        return "polygon" if self._shape is not None else "radius"

    def distance_m(self, lat: float, lon: float) -> float:
        """0 inside the polygon, otherwise metres to its edge (or to the centre in radius mode)."""
        if self._shape is not None:
            p = Point(lon, lat)
            if self._shape.contains(p):
                return 0.0
            # approximate degrees→metres near the campus latitude
            d_deg = self._shape.exterior.distance(p) if isinstance(self._shape, Polygon) else self._shape.distance(p)
            return d_deg * 111_320.0 * max(math.cos(math.radians(lat)), 0.2)
        return max(0.0, haversine_km(lat, lon, *self.center) * 1000.0 - self.radius_m)

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_m(lat, lon) == 0.0

    def bbox(self, pad_m: float = 0.0) -> list[float]:
        if self._shape is not None:
            minx, miny, maxx, maxy = self._shape.bounds
            return bbox_from_points([(miny, minx), (maxy, maxx)], pad_m=pad_m)
        return bbox_around(self.center[0], self.center[1], self.radius_m + pad_m)


def polygon_from_ways(ways: list[list[tuple[float, float]]]) -> list[list[float]] | None:
    """Build the outer ring of a relation from its outer member ways ((lat, lon) lists)."""
    try:
        from shapely.geometry import LineString
        lines = [LineString([(lon, lat) for lat, lon in w]) for w in ways if len(w) >= 2]
        merged = unary_union(lines)
        polys = list(polygonize(merged))
        if not polys:
            return None
        biggest = max(polys, key=lambda p: p.area)
        return [[lat, lon] for lon, lat in biggest.exterior.coords]
    except Exception:
        return None
