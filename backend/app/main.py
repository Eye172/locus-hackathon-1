"""CampusLens API."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import cache, http
from .config import settings
from .models import CATEGORY_LABELS, SOURCE_LABELS, ExternalCandidates, Photo, PhotoCandidate, Profile
from .pipeline import orchestrator, vision
from .pipeline.resolve import index, resolve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("campuslens")

_running: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache.init()
    index.load()
    asyncio.create_task(vision.warmup())
    if settings.depth_warmup:
        from .pipeline import depth as depth_mod
        asyncio.create_task(depth_mod.warmup())
    yield
    await http.close()


app = FastAPI(title="CampusLens API", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"ok": True, "clip_ready": vision.clip.ready, "index": len(index.rows), "budget_s": settings.pipeline_budget_s}


@app.get("/api/sources")
async def sources():
    return {"sources": settings.sources_status(), "labels": SOURCE_LABELS, "categories": CATEGORY_LABELS}


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1, max_length=120)):
    cands = await resolve(q)
    return {"query": q, "candidates": [c.model_dump() for c in cands]}


@app.get("/api/mini/{qid}")
async def mini(qid: str):
    """Instant facts for hover cards and fly-to: coordinates from the index/profile, 3 best photos if a profile exists."""
    row = index.by_id.get(qid)
    p = await cache.get_profile(qid)
    if not row and not p:
        try:
            from .pipeline.sources import wikidata
            uni = await wikidata.entity(qid)
        except Exception:
            raise HTTPException(404, "unknown university")
        return {"qid": qid, "name": uni.name, "names": uni.names, "city": uni.city, "country": uni.country,
                "founded": uni.founded, "students": uni.students, "logo_url": uni.logo_url,
                "lat": uni.lat, "lon": uni.lon, "profile": None}
    u = p.university if p else None
    lat = (u.lat if u else None) or (row["coord"][0] if row and row.get("coord") else None)
    lon = (u.lon if u else None) or (row["coord"][1] if row and row.get("coord") else None)
    return {
        "qid": qid,
        "name": (u.name if u else None) or row.get("ru") or row.get("en"),
        "names": u.names if u else {k: row.get(k) for k in ("ru", "en", "kk") if row.get(k)},
        "city": (u.city if u else None) or (row or {}).get("city"),
        "country": u.country if u else None,
        "founded": u.founded if u else None, "students": u.students if u else None,
        "logo_url": u.logo_url if u else None, "lat": lat, "lon": lon,
        "profile": {"coverage": p.coverage.get("overall"), "generated_at": p.generated_at, "photos_total": len(p.photos),
                    "photos": [{"id": x.id, "thumb": x.thumb, "category": x.category} for x in p.photos[:3]]} if p else None,
    }


@app.get("/api/recent")
async def recent():
    return {"recent": await cache.list_recent(12)}


@app.get("/api/profile/{qid}")
async def get_profile(qid: str):
    p = await cache.get_profile(qid)
    if not p:
        raise HTTPException(404, "profile not generated yet")
    return p


def _sse(ev: dict) -> dict:
    return {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False, default=str)}


@app.get("/api/profile/{qid}/stream")
async def stream_profile(qid: str, refresh: bool = False):
    async def gen():
        if not refresh:
            cached = await cache.get_profile(qid)
            if cached:
                yield _sse({"type": "profile", "profile": cached.model_dump(), "cached": True, "elapsed_ms": 0})
                return
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(ev: dict) -> None:
            await queue.put(ev)

        task = asyncio.create_task(orchestrator.run(qid, emit))
        _running[qid] = task
        try:
            while True:
                ev = await queue.get()
                yield _sse(ev)
                if ev["type"] in ("profile", "error"):
                    break
        finally:
            _running.pop(qid, None)
            if not task.done():
                task.cancel()
            else:
                task.exception()  # retrieve to avoid warnings

    return EventSourceResponse(gen(), ping=10)


@app.post("/api/profile/{qid}/refresh")
async def refresh_profile(qid: str):
    await cache.delete_profile(qid)
    return {"ok": True}


async def _generate_silent(qid: str) -> Profile:
    async def emit(ev: dict) -> None:
        return None
    return await orchestrator.run(qid, emit)


@app.get("/api/compare")
async def compare(a: str, b: str):
    async def get(qid: str) -> Profile:
        p = await cache.get_profile(qid)
        return p or await _generate_silent(qid)
    pa, pb = await asyncio.gather(get(a), get(b))
    return {"a": pa, "b": pb}


class CompareIn(BaseModel):
    a: str
    b: str
    prefs: dict = {}


@app.post("/api/compare/ai")
async def compare_ai(body: CompareIn):
    from .pipeline import compare as cmp
    key = f"{body.a}|{body.b}|{json.dumps(body.prefs, sort_keys=True)}|{settings.active_llm()}"
    cached = await cache.kv_get("compare", key, max_age_s=86400)
    if cached:
        return cached
    pa, pb = await asyncio.gather(_profile_or_build(body.a), _profile_or_build(body.b))
    fa, fb = await asyncio.gather(cmp.fact_sheet(pa), cmp.fact_sheet(pb))
    result, provider = await cmp.ai_compare(fa, fb, body.prefs)
    out = {**result.model_dump(), "provider": provider, "facts": {"a": fa, "b": fb}}
    await cache.kv_set("compare", key, out)
    return out


class ChatIn(BaseModel):
    a: str
    b: str
    prefs: dict = {}
    messages: list[dict]


@app.post("/api/chat")
async def chat(body: ChatIn):
    from .pipeline import compare as cmp
    pa, pb = await asyncio.gather(_profile_or_build(body.a), _profile_or_build(body.b))
    fa, fb = await asyncio.gather(cmp.fact_sheet(pa), cmp.fact_sheet(pb))

    async def gen():
        try:
            async for chunk in cmp.chat(fa, fb, body.prefs, body.messages):
                yield {"event": "token", "data": json.dumps({"text": chunk}, ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps({"provider": settings.active_llm()})}
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": json.dumps({"message": f"{type(e).__name__}: {e}"}, ensure_ascii=False)}

    return EventSourceResponse(gen())


async def _profile_or_build(qid: str) -> Profile:
    p = await cache.get_profile(qid)
    return p or await _generate_silent(qid)


class FlagIn(BaseModel):
    qid: str
    photo_id: str
    reason: str | None = None


@app.post("/api/flag")
async def flag(body: FlagIn):
    await cache.add_flag(body.qid, body.photo_id, body.reason)
    flags = await cache.get_flags(body.qid)
    return {"ok": True, "flags": flags.get(body.photo_id, 0)}


async def _facts(qid: str):
    """University facts + campus from the cached profile, else a quick live lookup (≈1 s)."""
    p = await cache.get_profile(qid)
    if p:
        return p.university, p.campus
    from .pipeline import enrich as enrich_mod
    uni, _ = await enrich_mod.facts(qid)
    return uni, None


@app.get("/api/context/{qid}")
async def context(qid: str, refresh: bool = False):
    if not refresh:
        c = await cache.kv_get("context", qid, max_age_s=7 * 86400)
        if c:
            return c
    from .pipeline import city
    uni, campus = await _facts(qid)
    try:
        pack = await asyncio.wait_for(city.build(uni, campus), timeout=30)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"context unavailable: {type(e).__name__}")
    if _pack_has_data(pack):  # an empty pack means Overpass was down: serve it, but let the next visit retry
        await cache.kv_set("context", qid, pack)
    return pack


def _pack_has_data(pack: dict) -> bool:
    return bool(pack.get("poi")) or bool(pack.get("campus_buildings", {}).get("features"))


@app.get("/api/climate/{qid}")
async def climate(qid: str, refresh: bool = False):
    from .pipeline import climate as climate_mod
    uni, _ = await _facts(qid)
    if uni.lat is None:
        raise HTTPException(404, "no coordinates")
    pack = None if refresh else await cache.kv_get("climate", qid, max_age_s=30 * 86400)
    if not pack:
        try:
            pack = await asyncio.wait_for(climate_mod.build(uni.lat, uni.lon, uni.city), timeout=20)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"climate unavailable: {type(e).__name__}")
        await cache.kv_set("climate", qid, pack)
    live = await cache.kv_get("weather_now", qid, max_age_s=3600)
    if not live:
        live = await climate_mod.now(uni.lat, uni.lon)
        if live:
            await cache.kv_set("weather_now", qid, live)
    return {**pack, **(live or {})}


_COST: dict | None = None


@app.get("/api/cost/{city_qid}")
async def cost(city_qid: str):
    global _COST
    if _COST is None:
        path = settings.data_dir / "cost_of_living.json"
        _COST = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"cities": {}}
    row = _COST["cities"].get(city_qid)
    if not row:
        raise HTTPException(404, "no_data")
    total = sum(v for k, v in row["items"].items() if k != "rent_1room")
    return {**row, "city_qid": city_qid, "total_student_month_dorm": total, "total_student_month_rent": total - row["items"]["dorm"] + row["items"]["rent_1room"]}


@app.post("/api/ingest")
async def ingest(body: ExternalCandidates):
    """Accept photo candidates from another collector (e.g. the Node/Next.js part). They join the next build."""
    n = await cache.add_external(body.qid, body.collector, [c.model_dump() for c in body.candidates])
    await cache.delete_profile(body.qid)  # force a rebuild so the new candidates are verified
    return {"ok": True, "stored": n, "next": f"/api/profile/{body.qid}/stream"}


@app.get("/api/schema")
async def schema():
    """JSON Schemas of the shared data contract (feed them to json-schema-to-zod on the Node side)."""
    return {
        "profile": Profile.model_json_schema(),
        "photo": Photo.model_json_schema(),
        "candidate": PhotoCandidate.model_json_schema(),
        "external": ExternalCandidates.model_json_schema(),
    }


@app.get("/api/depth/{photo_id}.png")
async def depth(photo_id: str):
    """8-bit depth map (bright = near) for the parallax "3D photo"; generated lazily, cached on disk."""
    from .pipeline import depth as depth_mod
    try:
        path = await asyncio.wait_for(depth_mod.ensure(photo_id), timeout=60)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"depth unavailable: {type(e).__name__}")
    if not path:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/thumb/{photo_id}.jpg")
async def thumb(photo_id: str):
    path = settings.thumbs_dir / f"{photo_id}.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# Serve the built frontend when present (single-container deployment)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        target = _dist / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(_dist / "index.html")
