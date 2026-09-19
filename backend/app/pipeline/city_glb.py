"""Grey 3D buildings for the places where Google has no photorealistic 3D.

Google's 3D map is a real textured mesh in ~2 500 cities; everywhere else (all of Kazakhstan, for one) it is satellite
imagery draped over terrain - flat. There the campus scene gets the city's own buildings: every OpenStreetMap footprint
(the OpenFreeMap z14 tiles the pack already reads), extruded to its height, grey - merged into one glTF model per
~400 m chunk, which the page places with a Model3DElement where the camera looks. Merged because the map draws a mesh
of hundreds of buildings at full frame rate, while 3 000 separate Polygon3DElements already drop it to ~20 fps.

A model is in metres around its chunk's centre; each building stands on the ground at its own place (a grid of
Copernicus DEM heights), so slopes like Almaty's keep the buildings on the terrain rather than floating off it.
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

# grey that reads as grey on the map: its models get a sun but almost no ambient light, so a plain grey went from white
# roofs to black walls on the shaded side. A little emission stands in for the ambient light.
WALLS = {"color": (0.50, 0.52, 0.56, 1.0), "glow": (0.16, 0.17, 0.19)}
ROOFS = {"color": (0.40, 0.42, 0.45, 1.0), "glow": (0.05, 0.05, 0.06)}
SINK_M = 2.5          # walls start below the ground: Google's terrain and the DEM differ by a few metres
MIN_AREA_M2 = 12.0    # sheds and kiosks add triangles, not a city


def _glb(meshes: list[dict]) -> bytes:
    """meshes: [{"pos": float32 (n,3), "nrm": float32 (n,3), "idx": uint32 (m,), "color": (r,g,b,a)}] -> .glb bytes."""
    bin_parts: list[bytes] = []
    views, accessors, prims, materials = [], [], [], []
    offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        pad = (-len(data)) % 4
        bin_parts.append(data + b"\0" * pad)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        offset += len(data) + pad
        return len(views) - 1

    for m in meshes:
        pos, nrm, idx = m["pos"].astype("<f4"), m["nrm"].astype("<f4"), m["idx"].astype("<u4")
        if not len(idx):
            continue
        a_pos = len(accessors)
        accessors.append({"bufferView": add_view(pos.tobytes(), 34962), "componentType": 5126, "count": len(pos),
                          "type": "VEC3", "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})
        accessors.append({"bufferView": add_view(nrm.tobytes(), 34962), "componentType": 5126, "count": len(nrm), "type": "VEC3"})
        accessors.append({"bufferView": add_view(idx.tobytes(), 34963), "componentType": 5125, "count": len(idx), "type": "SCALAR"})
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": list(m["color"]), "metallicFactor": 0.0, "roughnessFactor": 0.92},
                          "emissiveFactor": list(m.get("glow", (0.0, 0.0, 0.0)))})
        prims.append({"attributes": {"POSITION": a_pos, "NORMAL": a_pos + 1}, "indices": a_pos + 2, "material": len(materials) - 1})
    body = b"".join(bin_parts)
    gltf = {"asset": {"version": "2.0", "generator": "CampusLense"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}], "meshes": [{"primitives": prims}], "materials": materials,
            "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(body)}]}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(body)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(body), 0x004E4942) + body)


def _extrude(polys: list[tuple[Polygon, float, float]]) -> list[dict]:
    """(footprint in metres east/north of the origin, base z, top z) -> a walls mesh and a roofs mesh.
    The map reads a model as x = east, y = north, z = up, in metres (checked with a test model)."""
    wp, wn, wi, rp, ri = [], [], [], [], []
    nw = nr = 0
    roofs = shapely.constrained_delaunay_triangles(np.array([p for p, _, _ in polys], dtype=object))
    for (poly, z0, z1), tris in zip(polys, roofs):
        for ring in [poly.exterior, *poly.interiors]:   # oriented: exterior counter-clockwise, holes clockwise
            c = np.asarray(ring.coords)[:, :2]
            a, b = c[:-1], c[1:]
            d = b - a
            length = np.hypot(d[:, 0], d[:, 1])
            keep = length > 0.05
            a, b, d, length = a[keep], b[keep], d[keep], length[keep]
            k = len(a)
            if not k:
                continue
            n = np.column_stack([d[:, 1] / length, -d[:, 0] / length, np.zeros(k)])   # outward
            quad = np.empty((k, 4, 3))
            quad[:, 0, :2], quad[:, 1, :2], quad[:, 2, :2], quad[:, 3, :2] = a, b, b, a
            quad[:, :2, 2], quad[:, 2:, 2] = z0, z1
            wp.append(quad.reshape(-1, 3))
            wn.append(np.repeat(n, 4, axis=0))
            base = nw + 4 * np.arange(k)[:, None]
            wi.append((base + np.array([0, 1, 2, 0, 2, 3])).ravel())
            nw += 4 * k
        t = np.array([np.asarray(g.exterior.coords)[:3, :2] for g in getattr(tris, "geoms", [])])
        if len(t):
            # counter-clockwise seen from above, so the roof faces up
            cross = (t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0])
            t[cross < 0] = t[cross < 0][:, ::-1]
            rp.append(np.column_stack([t.reshape(-1, 2), np.full(3 * len(t), z1)]))
            ri.append(np.arange(nr, nr + 3 * len(t)))
            nr += 3 * len(t)
    out = []
    if wp:
        out.append({"pos": np.concatenate(wp), "nrm": np.concatenate(wn), "idx": np.concatenate(wi), **WALLS})
    if rp:
        pos = np.concatenate(rp)
        out.append({"pos": pos, "nrm": np.tile([0.0, 0.0, 1.0], (len(pos), 1)), "idx": np.concatenate(ri), **ROOFS})
    return out


def buildings_glb(buildings: list[dict], origin: tuple[float, float], ground) -> tuple[bytes, int]:
    """buildings: tile buildings ({"shape": lon/lat Polygon, "h", "min_h"}); origin: (lat, lon) the model is placed
    at; ground(lat, lon) -> metres above (or below) the ground at the origin. Returns (.glb, buildings drawn)."""
    lat0, lon0 = origin
    my = 111_320.0
    mx = 111_320.0 * max(math.cos(math.radians(lat0)), 0.05)
    polys: list[tuple[Polygon, float, float]] = []
    for b in buildings:
        h = float(b.get("h") or 0) or 6.0
        low = float(b.get("min_h") or 0)
        if h <= low + 0.5:
            continue
        p = shapely.transform(b["shape"], lambda c: np.column_stack([(c[:, 0] - lon0) * mx, (c[:, 1] - lat0) * my]))
        if p.area < MIN_AREA_M2:
            continue
        # 35 cm inside the footprint: the highlighted campus and dorm prisms drawn over the same footprints stay
        # outside the grey walls instead of flickering through them
        inset = p.buffer(-0.35, join_style="mitre")
        if isinstance(inset, Polygon) and not inset.is_empty and inset.area > 0.5 * p.area:
            p = inset
        p = orient(shapely.simplify(p, 0.3), 1.0)
        if not isinstance(p, Polygon) or p.is_empty or not p.is_valid:
            continue
        c = b["shape"].centroid
        g = ground(c.y, c.x)
        polys.append((p, g + (low if low > 0 else -SINK_M), g + h))
    if not polys:
        return b"", 0
    return _glb(_extrude(polys)), len(polys)


TILE_Z = 14           # the OpenFreeMap tiles the pack reads: ~1.5-2.4 km a side
CHUNK_Z = 16          # one model per z16 tile (~0.4-0.6 km): the map culls a model by its origin alone, so a bigger
                      # model vanished while its own buildings were still on screen (tested: a whole-area model and
                      # one model with a node per 400 m cell both disappeared once their origin was behind the camera)
GRID = 6              # ground heights: a 6 x 6 grid over the z14 tile (one elevation request), bilinear in between


def tile_center(x: int, y: int, z: int = TILE_Z) -> tuple[float, float]:
    """(lat, lon) of the middle of tile z/x/y: where the page places a chunk's model."""
    n = 2 ** z
    lon = (x + 0.5) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 0.5) / n))))
    return lat, lon


def _tile_of(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


async def chunks_of_tile(x: int, y: int, skip: set[str]) -> tuple[dict[tuple[int, int], tuple[bytes, int]], bool]:
    """The grey buildings of z14 tile x/y as one model per z16 chunk {(x16, y16): (.glb, buildings)}, each placed at
    its chunk's centre; a building goes to the chunk its centre is in. Empty chunks are left out. Also returns whether
    every ground height was known (without them the buildings stand on a flat plane). `skip`: building ids the scene
    draws itself, highlighted (campus, dorms)."""
    from . import map3d
    data = await map3d._tile_bytes(x, y)  # noqa: SLF001 - usually on disk already (the pack read it)
    if not data:
        return {}, True
    n2 = 2 ** TILE_Z
    w, e = x / n2 * 360.0 - 180.0, (x + 1) / n2 * 360.0 - 180.0
    n = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n2))))
    s = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n2))))
    lats, lons = np.linspace(s, n, GRID), np.linspace(w, e, GRID)
    heights = await map3d.elevations([(la, lo) for la in lats for lo in lons])
    z = np.array([np.nan if v is None else v for v in heights], dtype=float).reshape(GRID, GRID)
    dem_ok = bool(np.isfinite(z).all())
    z = np.where(np.isnan(z), np.nanmean(z) if np.isfinite(z).any() else 0.0, z)

    def dem(lat: float, lon: float) -> float:
        fy = min(max((lat - s) / (n - s), 0.0), 1.0) * (GRID - 1)
        fx = min(max((lon - w) / (e - w), 0.0), 1.0) * (GRID - 1)
        i, j = min(int(fy), GRID - 2), min(int(fx), GRID - 2)
        ty, tx = fy - i, fx - j
        return float((z[i, j] * (1 - tx) + z[i, j + 1] * tx) * (1 - ty) + (z[i + 1, j] * (1 - tx) + z[i + 1, j + 1] * tx) * ty)

    def work() -> dict[tuple[int, int], tuple[bytes, int]]:
        groups: dict[tuple[int, int], list[dict]] = {}
        for b in map3d.Tile(x, y, data).buildings():
            if b["id"] in skip:
                continue
            c = b["shape"].centroid
            groups.setdefault(_tile_of(c.y, c.x, CHUNK_Z), []).append(b)
        out = {}
        for (cx, cy), blds in groups.items():
            origin = tile_center(cx, cy, CHUNK_Z)
            at0 = dem(*origin)
            glb, count = buildings_glb(blds, origin, lambda la, lo: dem(la, lo) - at0)
            if count:
                out[(cx, cy)] = (glb, count)
        return out

    return await map3d._in_pool(work), dem_ok  # noqa: SLF001 - the map's own worker, not the busy default pool
