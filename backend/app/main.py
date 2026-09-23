"""CampusLense API."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
from contextlib import aclosing, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES as GZIP_SKIP
from starlette.middleware.gzip import GZipMiddleware

from . import cache, http
from .config import settings
from .models import CATEGORY_LABELS, SOURCE_LABELS, ExternalCandidates, Photo, PhotoCandidate, Profile
from .pipeline import og, orchestrator, vision
from .pipeline.names import row_en, row_name
from .pipeline.resolve import index, resolve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("campuslens")

class _Build:
    """One live build of a profile, shared by every page that asks for it while it runs. Once the first profile is
    out the build belongs to the server: closing the page does not stop the background pass (it saves what it finds),
    and a page opened later joins it - the events so far are replayed - instead of starting a second build."""

    def __init__(self, qid: str) -> None:
        self.events: list[dict] = []
        self.subs: set[asyncio.Queue] = set()
        self.answered = False
        self.closed = False    # the final profile or an error has gone out
        self.task = asyncio.create_task(orchestrator.run(qid, self.emit))
        self.task.add_done_callback(lambda t: self._done(qid, t))

    def _done(self, qid: str, t: asyncio.Task) -> None:
        if _builds.get(qid) is self:
            del _builds[qid]
        if not t.cancelled():
            t.exception()  # retrieved: a failure normally has already gone out as an "error" event
        if not self.closed:  # stopped without a last word (cancelled, or died oddly): nobody may wait forever
            self._send({"type": "error", "message": "сборка профиля остановлена", "log": []})

    async def emit(self, ev: dict) -> None:
        self._send(ev)

    def _send(self, ev: dict) -> None:
        if ev["type"] == "profile":  # a joining page needs only the latest one
            self.answered = True
            self.events = [e for e in self.events if e["type"] != "profile"]
        self.closed = ev["type"] == "error" or (ev["type"] == "profile" and ev.get("final", True))
        self.events.append(ev)
        for q in self.subs:
            q.put_nowait(ev)


_builds: dict[str, _Build] = {}


async def _build_events(qid: str):
    """Events of the live build of `qid`, started here or joined; ends after the final profile or an error."""
    b = _builds.get(qid)
    if b is None:
        b = _builds[qid] = _Build(qid)
    q: asyncio.Queue = asyncio.Queue()
    for ev in b.events:
        q.put_nowait(ev)
    b.subs.add(q)
    try:
        while True:
            ev = await q.get()
            yield ev
            if ev["type"] == "error" or (ev["type"] == "profile" and ev.get("final", True)):
                return
    finally:
        b.subs.discard(q)
        # left before anything was shown: nobody is waiting for it; after the first profile it runs to the end
        if not b.subs and not b.answered:
            b.task.cancel()


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


app = FastAPI(title="CampusLense API", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
# JSON answers shrink 5-7x (a profile: 980 KB -> 140 KB). SSE is compressed too: Starlette flushes every event
# (Z_SYNC_FLUSH), so the stream stays live. Photos and files already gzipped on disk are left as they are.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5,
                   exclude_content_types=tuple(t for t in GZIP_SKIP if t != "text/event-stream"))
from .api_search import router as _search_router  # noqa: E402  (search settings and campus facts)
app.include_router(_search_router)


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
        if qid.startswith("W"):
            ent = await cache.kv_get("web", qid)
            if not ent:
                raise HTTPException(404, "unknown university")
            from .pipeline.names import web_display
            return {"qid": qid, "name": web_display(ent["name"], ent.get("name_en"), ent.get("country")), "name_en": ent.get("name_en"), "country_qid": None,
                    "names": {}, "city": ent.get("city"), "country": None, "founded": None,
                    "students": None, "logo_url": None, "lat": ent.get("lat"), "lon": ent.get("lon"), "profile": None,
                    "website": ent.get("website"), "origin": "web"}
        try:
            from .pipeline.sources import wikidata
            uni = await wikidata.entity(qid)
        except Exception:
            raise HTTPException(404, "unknown university")
        return {"qid": qid, "name": uni.name, "name_en": uni.name_en, "country_qid": uni.country_qid, "names": uni.names, "city": uni.city, "country": uni.country,
                "founded": uni.founded, "students": uni.students, "logo_url": uni.logo_url,
                "lat": uni.lat, "lon": uni.lon, "profile": None}
    u = p.university if p else None
    lat = (u.lat if u else None) or (row["coord"][0] if row and row.get("coord") else None)
    lon = (u.lon if u else None) or (row["coord"][1] if row and row.get("coord") else None)
    if lat is None:
        # an index row without coordinates (Wikidata has none): geocode quickly so the globe can still fly there
        try:
            from .pipeline import enrich as enrich_mod
            uni, _ = await asyncio.wait_for(enrich_mod.facts(qid), timeout=5.0)
            lat, lon = uni.lat, uni.lon
            if row is not None and lat is not None:
                row["coord"] = [lat, lon]  # remember for this process
        except Exception:  # noqa: BLE001
            pass
    return {
        "qid": qid,
        "name": u.name if u else row_name(row),
        "name_en": (u.name_en if u else None) or (row_en(row) if row else None),
        "country_qid": (u.country_qid if u else None) or (row or {}).get("country"),
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
        # A profile is never served as the answer just because it is in the cache: the case is "find it now", and a
        # university that was opened yesterday has posted since. What the cache is good for is the wait - it goes out
        # first, marked as the previous run, and the live build replaces it the moment it is ready.
        if not refresh:
            cached = await cache.get_profile(qid)
            if cached:
                yield _sse({"type": "profile", "profile": cached.model_dump(), "cached": True, "final": False,
                            "elapsed_ms": 0})
        # the pipeline sends the first profile, then keeps collecting in the background and sends it again as it
        # grows; the stream closes on the one marked final
        async with aclosing(_build_events(qid)) as events:
            async for ev in events:
                yield _sse(ev)

    return EventSourceResponse(gen(), ping=10)


@app.post("/api/profile/{qid}/refresh")
async def refresh_profile(qid: str):
    await cache.delete_profile(qid)
    return {"ok": True}


async def _generate_silent(qid: str) -> Profile:
    """The first profile of a live build; its background pass goes on without the caller."""
    async with aclosing(_build_events(qid)) as events:
        async for ev in events:
            if ev["type"] == "error":
                raise HTTPException(502, ev["message"])
            if ev["type"] == "profile":
                return Profile.model_validate(ev["profile"])
    raise HTTPException(502, "profile build stopped")


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


_advisor_sheets: dict[tuple[str, str], tuple[float, dict]] = {}


async def _advisor_sheet(qid: str, lang: str) -> dict:
    """The advisor's facts about one university, kept for 10 minutes (the cards and every chat turn use them)."""
    from .pipeline import compare as cmp
    hit = _advisor_sheets.get((qid, lang))
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    sheet = await cmp.advisor_sheet(await _profile_or_build(qid), lang)
    _advisor_sheets[(qid, lang)] = (time.monotonic(), sheet)
    return sheet


@app.get("/api/digest/{qid}")
async def site_digest_endpoint(qid: str):
    """Facts about the university from its official site and Wikipedia (sports, food, housing, clubs, costs...)."""
    from .pipeline import site_digest
    d = await site_digest.digest((await _profile_or_build(qid)).university, wait=90)
    if d is None:
        raise HTTPException(503, "digest not ready")
    return d


@app.get("/api/compare/sheets")
async def compare_sheets(a: str, b: str, lang: str = Query("ru", pattern="^(ru|en|kk)$")):
    sa, sb = await asyncio.gather(_advisor_sheet(a, lang), _advisor_sheet(b, lang))
    return {"a": sa, "b": sb}


class AdvisorIn(BaseModel):
    a: str
    b: str
    lang: str = "ru"
    messages: list[dict] = []


@app.post("/api/compare/advisor")
async def compare_advisor(body: AdvisorIn):
    """The compare page's chat, streamed. With no messages it writes the opening message - strengths and weak
    points of both and the main differences - which is cached per pair and language."""
    from .pipeline import compare as cmp
    lang = body.lang if body.lang in cmp.LANG_NAME else "ru"
    intro_key = f"v{cmp.ADVISOR_VERSION}:{body.a}|{body.b}|{lang}"

    async def gen():
        try:
            if not body.messages:
                hit = await cache.kv_get("compare_intro", intro_key, max_age_s=3 * 86400)
                if hit and hit.get("text"):
                    text = hit["text"]
                    for i in range(0, len(text), 60):      # the saved intro still types itself out, quickly
                        yield {"event": "token", "data": json.dumps({"text": text[i:i + 60]}, ensure_ascii=False)}
                        await asyncio.sleep(0.012)
                    yield {"event": "done", "data": json.dumps({"cached": True})}
                    return
            sa, sb = await asyncio.gather(_advisor_sheet(body.a, lang), _advisor_sheet(body.b, lang))
            out = []
            async for chunk in cmp.advisor_stream(sa, sb, body.messages, lang):
                out.append(chunk)
                yield {"event": "token", "data": json.dumps({"text": chunk}, ensure_ascii=False)}
            if not body.messages and "".join(out).strip():
                await cache.kv_set("compare_intro", intro_key, {"text": "".join(out)})
            yield {"event": "done", "data": json.dumps({"cached": False})}
        except Exception as e:  # noqa: BLE001
            log.warning("advisor failed: %r", e)
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


_facts_live: dict[str, tuple[float, asyncio.Task]] = {}


async def _facts(qid: str):
    """University facts + campus from the cached profile, else a live lookup (1.5-2.5 s, Wikidata + Wikipedia).
    The live lookup is shared for an hour: the 3D map asks again while its city boundary is pending."""
    p = await cache.get_profile(qid)
    if p:
        return p.university, p.campus
    from .pipeline import enrich as enrich_mod
    hit = _facts_live.get(qid)
    t = hit[1] if hit else None
    if t is None or time.monotonic() - hit[0] > 3600 or (t.done() and (t.cancelled() or t.exception() is not None)):
        t = asyncio.ensure_future(enrich_mod.facts(qid))
        _facts_live[qid] = (time.monotonic(), t)
    uni, _ = await asyncio.shield(t)
    return uni.model_copy(deep=True), None


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


@app.get("/api/map3d/{qid}")
async def map3d_pack(qid: str, refresh: bool = False, lang: str = Query("ru", pattern="^(ru|en|kk)$")):
    """Everything the Google 3D campus page draws: outline, campus and dorm footprints, OSM places, city centre."""
    key = f"{qid}:{lang}"
    if not refresh:
        c = await cache.kv_get("map3d", key, max_age_s=7 * 86400)
        from .pipeline.map3d import PACK_VERSION
        if c and c.get("v") == PACK_VERSION:
            row = index.by_id.get(qid)
            if row:  # names as the globe and the search show them, also in packs saved under older naming rules
                c["university"].update(name=row_name(row), name_en=row_en(row), country_qid=row.get("country"))
            return c
    # requests for a pack that is being built join that build (the scene and its retries ask at the same time)
    t = _pack_builds.get(key)
    if t is None:
        t = _pack_builds[key] = asyncio.ensure_future(_build_pack(qid, key, lang))
        t.add_done_callback(lambda _t: _pack_builds.pop(key, None))
    return await asyncio.shield(t)


_pack_builds: dict[str, asyncio.Task] = {}


@app.get("/api/map3d/{qid}/city")
async def map3d_city(qid: str, lang: str = Query("ru", pattern="^(ru|en|kk)$")):
    """Only the city boundary, for a scene whose pack went out without it (city_status "pending"). The page polls this
    during the orbit instead of downloading and parsing the whole pack again every few seconds."""
    from .pipeline import map3d
    task = map3d._tasks.get(("city", qid))
    if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
        pack = await map3d_pack(qid, refresh=False, lang=lang)   # starts (or restarts) the lookup
        return {"city_area": pack.get("city_area"), "city_status": pack.get("city_status")}
    try:
        city = await asyncio.wait_for(asyncio.shield(task), timeout=3)
    except Exception:  # noqa: BLE001  (still looking, or failed: the next poll restarts it)
        return {"city_area": None, "city_status": "pending"}
    # the pack with its city is worth keeping for the next visit: built once more in the background (its parts are cached)
    if not await cache.kv_get("map3d", f"{qid}:{lang}", max_age_s=7 * 86400):
        asyncio.ensure_future(map3d_pack(qid, refresh=False, lang=lang))
    return {"city_area": city, "city_status": "ok" if city else "none"}


async def _build_pack(qid: str, key: str, lang: str) -> dict:
    from .pipeline import map3d
    p = await cache.get_profile(qid)
    if p:
        uni, campus = p.university, p.campus
        photos = [ph.model_dump(include={"id", "lat", "lon", "thumb", "category", "level", "page_url", "source_label", "rejected"})
                  for ph in p.photos]
    else:
        row = index.by_id.get(qid)
        if row and row.get("coord"):  # tiles and the campus outline load while Wikidata answers
            map3d.warm(qid, row["coord"][0], row["coord"][1], [row.get("ru"), row.get("en")])
        uni, campus = await _facts(qid)
        photos = []
    if uni.lat is None:
        raise HTTPException(404, "no coordinates")
    try:
        pack = await asyncio.wait_for(map3d.build(uni, campus, photos, lang), timeout=40)
    except Exception as e:  # noqa: BLE001
        log.exception("map3d failed for %s", qid)
        raise HTTPException(503, f"map3d unavailable: {type(e).__name__}")
    # not kept: a pack still waiting for its city, or built while the elevation services refused (no ground height)
    if pack["stats"]["tiles"] and pack.get("city_status") != "pending" and pack["anchor"].get("elevation") is not None:
        await cache.kv_set("map3d", key, pack)
    return pack


class FootprintsBody(BaseModel):
    points: list[dict]


@app.post("/api/map3d/footprints")
async def map3d_footprints(body: FootprintsBody):
    """Building footprint and ground height under each point (dormitories found by Google Places on the client)."""
    from .pipeline import map3d
    return {"items": await map3d.footprints(body.points)}


GLB_VERSION = 6  # in the model URL too: a changed model must not come from week-long browser caches
GREY_MAX_KM = 40  # tiles are served only around the university's city (the camera is locked to it anyway)
_grey_builds: dict[str, asyncio.Task] = {}
_grey_skip: dict[str, tuple[set[str], tuple[float, float]]] = {}


async def _grey_tile(qid: str, x: int, y: int) -> list[list[int]]:
    """Builds (once) the grey chunk models of z14 tile x/y for this university's scene: [[x16, y16, buildings], ...]."""
    import gzip
    from .geo import haversine_km
    from .pipeline import city_glb
    if qid not in _grey_skip:
        pack = await map3d_pack(qid, refresh=False, lang="ru")
        skip = {b["id"] for b in pack["campus"]["buildings"]} |                {d["building"]["id"] for d in pack.get("dorms") or [] if d.get("building")}
        _grey_skip[qid] = (skip, (pack["anchor"]["lat"], pack["anchor"]["lon"]))
    skip, anchor = _grey_skip[qid]
    if haversine_km(*city_glb.tile_center(x, y), *anchor) > GREY_MAX_KM:
        raise HTTPException(400, "only the university's city")
    folder = settings.data_dir / "glb" / qid / f"v{GLB_VERSION}"
    index = folder / f"{x}_{y}.json"
    # a tile built without ground heights (the elevation services refused) stands flat: rebuilt after a while
    stale = index.exists() and not json.loads(index.read_text()).get("dem", True) and time.time() - index.stat().st_mtime > 600
    if not index.exists() or stale:
        async def build() -> None:
            chunks, dem = await city_glb.chunks_of_tile(x, y, skip)
            folder.mkdir(parents=True, exist_ok=True)
            for (cx, cy), (glb, _) in chunks.items():
                (folder / f"{cx}_{cy}.glb.gz").write_bytes(gzip.compress(glb, 6))
            index.write_text(json.dumps({"chunks": [[cx, cy, n] for (cx, cy), (_, n) in chunks.items()], "dem": dem}))
        key = f"{qid}/{x}/{y}"
        t = _grey_builds.get(key)
        if t is None:
            t = _grey_builds[key] = asyncio.ensure_future(build())
            t.add_done_callback(lambda _t: _grey_builds.pop(key, None))
        await asyncio.shield(t)
    return json.loads(index.read_text())["chunks"]


@app.get("/api/map3d/{qid}/grey/{x}/{y}.json")
async def map3d_grey_index(qid: str, x: int, y: int, response: Response):
    """Grey 3D buildings where Google's 3D map is flat satellite imagery (pipeline/city_glb.py): the non-empty ~400 m
    chunks of z14 tile x/y. The page asks for the tiles where the camera looks, then loads their chunks' models."""
    chunks = await _grey_tile(qid, x, y)
    # a revisit of the scene takes the ~50 tile lists from the browser; 10 min = the flat-tile rebuild window above
    response.headers["Cache-Control"] = "public, max-age=600"
    return {"z": 16, "chunks": chunks}


# the path ends in ".glb", no query: the map's model loader silently ignores any other src
@app.get("/api/map3d/{qid}/grey16/{cx}/{cy}-v{ver}.glb")
async def map3d_grey_chunk(qid: str, cx: int, cy: int, ver: int, request: Request):
    """One chunk's model (z16 tile cx/cy), placed by the page at the chunk's centre. The campus's own buildings and
    dorms are left out: the scene draws them highlighted. Gzipped on disk."""
    import gzip
    path = settings.data_dir / "glb" / qid / f"v{GLB_VERSION}" / f"{cx}_{cy}.glb.gz"
    if not path.exists():
        await _grey_tile(qid, cx >> 2, cy >> 2)   # its z14 tile (z16 = z14 + 2)
    if not path.exists():
        return Response(status_code=204)
    body = path.read_bytes()
    headers = {"Cache-Control": "public, max-age=604800"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(body, media_type="model/gltf-binary", headers={**headers, "Content-Encoding": "gzip"})
    return Response(gzip.decompress(body), media_type="model/gltf-binary", headers=headers)


_climate_builds: dict[str, asyncio.Task] = {}


async def _climate_pack(qid: str, refresh: bool = False):
    """The year's climate pack. One build per university at a time (the story and the tab ask together); it runs on
    even if the page is closed. When a rebuild fails, an older saved pack is better than an error."""
    from .pipeline import climate as climate_mod
    uni, _ = await _facts(qid)
    if uni.lat is None:
        raise HTTPException(404, "no coordinates")
    old = await cache.kv_get("climate", qid)
    pack = None if refresh else await cache.kv_get("climate", qid, max_age_s=30 * 86400)
    if pack and pack.get("v") == climate_mod.PACK_VERSION:
        return uni, pack
    task = _climate_builds.get(qid)
    if task is None or task.done():
        async def run():
            p = await climate_mod.build(uni.lat, uni.lon, uni.city)
            await cache.kv_set("climate", qid, p)
            return p
        task = _climate_builds[qid] = asyncio.ensure_future(run())
    try:
        return uni, await asyncio.wait_for(asyncio.shield(task), timeout=75)
    except Exception as e:  # noqa: BLE001
        if old:
            return uni, old
        raise HTTPException(503, f"climate unavailable: {type(e).__name__}")


@app.get("/api/climate/{qid}/story")
async def climate_story(qid: str, lang: str = "ru"):
    """How the climate feels for a student: written by the LLM from the climate pack's own numbers, cached per language."""
    from .pipeline import climate as climate_mod
    lang = lang if lang in climate_mod.LANG_NAME else "ru"
    uni, pack = await _climate_pack(qid)
    key = f"v{climate_mod.STORY_VERSION}:{qid}:{lang}:{pack['year']}"
    hit = await cache.kv_get("climate_story", key, max_age_s=30 * 86400)
    if hit:
        return hit
    out = await climate_mod.story(pack, uni.name, uni.city, lang)
    if out["mode"] == "ai":
        await cache.kv_set("climate_story", key, out)
    return out


@app.get("/api/climate/{qid}")
async def climate(qid: str, refresh: bool = False):
    from .pipeline import climate as climate_mod
    uni, pack = await _climate_pack(qid, refresh)
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


@app.get("/api/hero/{qid}/{photo_id}.jpg")
async def hero(qid: str, photo_id: str, src: str | None = None):
    """Large copy (≤1600 px) of a profile photo for the arrival scene; fetched from the original once, cached on disk.
    Falls back to the 640 px thumbnail when the original is unavailable or the profile is still being built."""
    hero_dir = settings.hero_dir() if callable(settings.hero_dir) else settings.hero_dir
    path = hero_dir / f"{photo_id}.jpg"
    thumb_path = settings.thumbs_dir / f"{photo_id}.jpg"
    if not path.exists():
        p = await cache.get_profile(qid)
        ph = next((x for x in (p.photos if p else []) if x.id == photo_id), None)
        # while the profile is still being built the client knows the original URL from the stream; trust it only
        # when it hashes to this photo id (ids are sha1(url)[:16]), so nothing foreign can be proxied
        from .pipeline.fetch import photo_id as _pid
        url = ph.url if ph else (src if src and src.startswith("http") and _pid(src) == photo_id else None)
        if url and url.startswith("file:"):
            # a frame we extracted ourselves: the full-size file is already on disk
            frame = Path(url[5:])
            if frame.exists():
                path = frame
            url = None
        if url:
            try:
                r = await http.get(url, timeout=8.0)
                if r.status_code == 200 and r.content:
                    def _make(data: bytes) -> bytes:
                        from io import BytesIO
                        from PIL import Image, ImageOps
                        im = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
                        im.thumbnail((1600, 1600))
                        out = BytesIO(); im.save(out, "JPEG", quality=86, optimize=True); return out.getvalue()
                    path.write_bytes(await asyncio.get_running_loop().run_in_executor(None, _make, r.content))
            except Exception as e:  # noqa: BLE001
                log.info("hero fetch failed for %s: %s", photo_id, e)
    if not path.exists():
        if not thumb_path.exists():
            raise HTTPException(404)
        path = thumb_path
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/thumb/{photo_id}.jpg")
async def thumb(photo_id: str):
    path = settings.thumbs_dir / f"{photo_id}.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/og/{qid}.png")
async def og_card(qid: str):
    """1200x630 preview card for messengers and social networks: cover photo, name, city, verified count."""
    p = await cache.get_profile(qid)
    row = index.by_id.get(qid)
    if not p and not row:
        raise HTTPException(404)
    png = await asyncio.get_running_loop().run_in_executor(None, og.render, p, row)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/s/{qid}", response_class=HTMLResponse)
async def share_page(qid: str, tab: str = "photos", photo: str | None = None):
    """Link-preview page: crawlers read the OG tags, humans are redirected to the app."""
    p = await cache.get_profile(qid)
    row = index.by_id.get(qid)
    if not p and not row:
        raise HTTPException(404)
    name = (p.university.name if p else None) or (row_name(row) if row else qid)
    city = (p.university.city if p else None) or (row.get("city") if row else None)
    verified = sum(1 for ph in p.photos if ph.level == "verified") if p else 0
    desc = og.description(p, row)
    target = f"{settings.frontend_origin}/u/{qid}?tab={tab}" + (f"&photo={photo}" if photo else "")
    esc = lambda x: (x or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")  # noqa: E731
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{esc(name)} · CampusLense</title>
<meta property="og:type" content="website"><meta property="og:site_name" content="CampusLense">
<meta property="og:title" content="{esc(name)}{(' · ' + esc(city)) if city else ''}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="/api/og/{qid}.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta http-equiv="refresh" content="0; url={esc(target)}">
</head><body style="font-family:system-ui;padding:24px"><a href="{esc(target)}">{esc(name)}</a> — {verified} подтверждённых фото. Открываем профиль…</body></html>"""
    return HTMLResponse(html)


# Serve the built frontend when present (single-container deployment)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_HASHED = re.compile(r"-[A-Za-z0-9_-]{8}\.(?:js|css)$")   # Vite's content-hashed names: never change, cache for a year
_TYPES = {".geojson": "application/geo+json", ".mjs": "text/javascript", ".pbf": "application/x-protobuf"}


def _static(target: Path, request: Request) -> Response:
    """A built file. The build writes .br/.gz copies next to text files (vite.config.ts, precompress): the smallest
    one the browser takes is sent as is, with no work per request. Unchanged files answer 304 on revalidation."""
    if target.name == "index.html":
        cache_control = "no-cache"   # always revalidated: it names the current hashed bundles
    elif _HASHED.search(target.name):
        cache_control = "public, max-age=31536000, immutable"
    else:
        cache_control = "public, max-age=3600"   # public/ files keep their names (geojson, planet tiles, geo packs)
    headers = {"Cache-Control": cache_control, "Vary": "Accept-Encoding"}
    media_type = _TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    accept = request.headers.get("accept-encoding", "")
    served = target
    for enc, ext in (("br", ".br"), ("gzip", ".gz")):
        alt = target.with_name(target.name + ext)
        if enc in accept and alt.is_file():
            served, headers["Content-Encoding"] = alt, enc
            break
    st = served.stat()
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control, "Vary": "Accept-Encoding"})
    return FileResponse(served, media_type=media_type, headers={**headers, "ETag": etag})


if _dist.exists():
    @app.get("/{path:path}")
    async def spa(path: str, request: Request):
        # an unknown API path is an API error, never the app's HTML (the client would fail on "<!doctype")
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, f"no such API endpoint: /{path}")
        # One site, not two: while a dev server (FRONTEND_ORIGIN) is running, every page goes there and only /api
        # stays here; in production FRONTEND_ORIGIN is empty and the built app is served from this container.
        if settings.frontend_origin:
            q = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"{settings.frontend_origin}/{path}{q}", status_code=307)
        target = (_dist / path).resolve()
        if not target.is_relative_to(_dist.resolve()):
            raise HTTPException(404, "file not found")
        if path and target.is_file():
            return _static(target, request)
        if path.startswith("assets/"):   # a missing script must fail as a script, not arrive as the app's HTML
            raise HTTPException(404, "file not found")
        return _static(_dist / "index.html", request)
