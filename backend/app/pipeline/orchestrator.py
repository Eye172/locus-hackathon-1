"""Runs the whole pipeline for one university and streams events.

Event types: stage, university, campus, source, photos (preliminary batches), profile, error.
Design: facts come first (≈1 s) so the UI can render the header; the campus outline (Overpass) is resolved
in parallel with the non-geographic sources; geo sources wait for it. The first profile is a snapshot at ~24 s
(the case's 30 s); nothing is cut off there - the background pass carries on without a clock and sends the grown
profile again (final=False) until the last one (final=True).
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlparse

import numpy as np
from PIL import Image

from .. import cache, http
from ..config import settings
from ..geo import CampusGeom
from ..models import (BROCHURE_SOURCES, CATEGORIES, Campus, CategoryStats, Photo, PhotoCandidate, Profile,
                      SOURCE_LABELS, Stage, University)
from . import categorize as categorize_mod
from . import context as context_mod
from . import collage as collage_mod
from . import cover as cover_mod
from . import crossdup, curate, dedup, describe, search_learn, search_plan, video_frames, vision
from . import enrich as enrich_mod
from .ai_inspector import Inspector
from .fetch import Fetched, fetch_all, fetch_waves
from .fetch import photo_id as fetch_id
from .sources import (social, social_api, social_search, commons, flickr, map_reviews, mapillary, official_site,
                      places, vk_geo, web_images, wikipedia)
from .verify import CITY_SOURCES, CROWD_SOURCES, SEARCH_SOURCES, VerifyContext, hard_flags, score

log = logging.getLogger("campuslens.orchestrator")
Emit = Callable[[dict], Awaitable[None]]

STAGES = [
    ("facts", "Факты о вузе"),
    ("campus", "Границы кампуса (OSM)"),
    ("collect", "Сбор кандидатов из источников"),
    ("fetch", "Загрузка изображений"),
    ("inspect", "ИИ-инспектор: проверка каждого фото"),
    ("analyze", "Уверенность, дубли, отбор"),
    ("assemble", "Сборка профиля"),
]
GEO_SOURCES = {"commons_geo", "mapillary", "flickr"}
HARD_CAP_S = 29.0  # the case asks for a useful profile within 30 s: nothing optional may push past this
# with the background pass on, the first profile does not have to wait for stragglers - they arrive in later ones
FAST_TARGET_S = 24.0
SOCIAL_SOURCES = {"telegram", "youtube", "instagram", "instagram_tagged", "tiktok", "vk"}  # wait for the site links


class Run:
    def __init__(self, qid: str, emit: Emit) -> None:
        self.qid = qid
        self.emit = emit
        self.t0 = time.monotonic()
        self.deadline = self.t0 + settings.pipeline_budget_s
        self.stages: dict[str, Stage] = {k: Stage(key=k, label=l) for k, l in STAGES}
        self.sources_status: dict[str, dict] = {}
        self.log: list[str] = []
        self.fetched: list[Fetched] = []
        self.embs: dict[str, np.ndarray] = {}
        self.clf: dict[str, dict] = {}
        self.partial = False
        self.uni: University | None = None
        self.campus: Campus | None = None
        self.vctx: VerifyContext | None = None
        self.campus_task: asyncio.Task | None = None
        self.inspector: Inspector | None = None
        self.ref_emb: np.ndarray | None = None
        self.reference: dict | None = None
        self.deep = settings.deep_pass
        self.description = None
        self.context = None
        self.plan: search_plan.Plan = search_plan.load()
        self.collect_deadline = self.deadline
        self.factories: dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]] = {}
        self.first_tasks: dict[str, asyncio.Task] = {}   # the first pass's source runs, by source name
        self.got: dict[str, int] = {}                    # images each source has added, over all its runs
        self.have: set[tuple[str, str]] = set()          # (source, photo id): a re-run never adds the same image twice
        # after the first profile: no stage bar, no timer, no per-source chatter - only the grown profile now and then
        self.quiet = False
        self.first_ms: int | None = None
        self.guard_end = math.inf
        self.bg_done = asyncio.Event()
        # the cover editor (pipeline/cover.py): its last answer, for which candidates, the request in flight
        self.cover_pick: cover_mod.Pick | None = None
        self.cover_key: str | None = None
        self.cover_task: asyncio.Task | None = None
        self.final_started = False

    # ---------- helpers ----------
    @property
    def cap_s(self) -> float:
        return FAST_TARGET_S if self.deep else HARD_CAP_S

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def until(self, t_s: float) -> float:
        """Seconds left until t_s after the start (never negative)."""
        return max(0.0, self.t0 + t_s - time.monotonic())

    def logf(self, s: str) -> None:
        self.log.append(f"[{self.ms():>6} ms] {s}")
        log.info("%s %s", self.qid, s)

    async def stage(self, key: str, status: str, detail: str | None = None, count: int | None = None) -> None:
        if self.quiet:  # the stage bar stays as the first profile left it
            return
        st = self.stages[key]
        if status == "running":
            st.ms = self.ms()
        elif status in ("done", "skipped", "error"):
            st.ms = self.ms() - (st.ms or 0)
        st.status, st.detail, st.count = status, detail, count
        await self.emit({"type": "stage", "stage": st.model_dump(), "elapsed_ms": self.ms()})

    async def source_event(self, name: str, status: str, count: int = 0, ms: int = 0, detail: str | None = None) -> None:
        self.sources_status[name] = {"status": status, "count": count, "ms": ms, "detail": detail,
                                     "label": SOURCE_LABELS.get(name, name)}
        if self.quiet:  # kept for the next profile, not streamed
            return
        await self.emit({"type": "source", "name": name, **self.sources_status[name], "elapsed_ms": self.ms()})

    # ---------- photo building ----------
    def make_photo(self, f: Fetched) -> Photo:
        c = f.cand
        srcs = list(dict.fromkeys([c.source] + [x.source for x in f.extra_sources]))
        date, date_source = c.date, c.date_source
        if not date and f.exif_date:
            date, date_source = f.exif_date, "exif"
        if not date and c.source == "official" and f.last_modified:
            try:
                from email.utils import parsedate_to_datetime
                date, date_source = parsedate_to_datetime(f.last_modified).strftime("%Y-%m-%d"), "last-modified"
            except Exception:
                pass
        year = int(date[:4]) if date and date[:4].isdigit() else None
        lat, lon = c.lat, c.lon
        if lat is None and f.exif_lat is not None:
            lat, lon = f.exif_lat, f.exif_lon
        return Photo(
            id=f.id, url=c.url, page_url=c.page_url, thumb=f"/api/thumb/{f.id}.jpg",
            width=f.width, height=f.height, source=c.source, source_label=SOURCE_LABELS.get(c.source, c.source),
            sources=srcs, sources_count=len(srcs), title=c.title, author=c.author, license=c.license,
            date=date, date_source=date_source, year=year,
            outdated=bool(year and year < datetime.now().year - settings.outdated_years),
            lat=lat, lon=lon, phash=f.phash, dhash=f.dhash or None, sha1=f.sha1 or None,
            is_brochure=c.source in BROCHURE_SOURCES,
            intent=c.intent or next((x.intent for x in f.extra_sources if x.intent), None),
            query=c.query or next((x.query for x in f.extra_sources if x.query), None),
        )

    def text_of(self, f: Fetched) -> str:
        return " ".join([f.cand.text or "", f.cand.title or ""] + [x.text or "" for x in f.extra_sources])

    def verdict_of(self, f: Fetched):
        if not self.inspector:
            return None
        v = self.inspector.verdicts.get(f.id)
        if v is None:  # a copy of this image may have been inspected under another URL
            v = next((self.inspector.verdicts[x] for x in (fetch_id(c.url) for c in f.extra_sources)
                      if x in self.inspector.verdicts), None)
        return v

    def analyze(self, items: list[Fetched]) -> list[Photo]:
        photos: list[Photo] = []
        buildings = self.campus.buildings if self.campus else []
        for f in items:
            p = self.make_photo(f)
            is_city = f.cand.is_city or f.cand.source in CITY_SOURCES
            categorize_mod.categorize(p, self.clf[f.id], self.text_of(f), is_city, buildings)
            v = self.verdict_of(f)
            if v and p.rejected and v.place in ("this_university", "city") and v.q >= 1 \
                    and not hard_flags(v) and p.reject_reason and p.reject_reason.startswith("не фотография кампуса"):
                # CLIP took a real photo (a banner on a building, a stage with text) for junk; the inspector looked closer
                p.rejected, p.reject_reason, p.junk_soft = False, None, True
            sim = float(np.dot(self.embs[f.id], self.ref_emb)) if self.ref_emb is not None and f.id in self.embs else None
            other = crossdup.elsewhere(p.phash, self.uni.qid, self.uni.lat or self.uni.city_lat,
                                       self.uni.lon or self.uni.city_lon)
            score(p, self.vctx, self.text_of(f), is_city, verdict=v, ref_sim=sim, elsewhere=other)
            photos.append(p)
        return photos

    def ai_priority(self, f: Fetched) -> float:
        """Which photos the inspector looks at first. Cheap signals only: how much CLIP thinks it is a photograph
        rather than a poster, map or screenshot; how much it resembles the reference photo of the main building; and
        whether the source needs the verdict at all - a search or crowd photo without one is never shown, an
        official one still is, with less confidence."""
        c = self.clf.get(f.id) or {}
        photo_like = 1.0 - float(c.get("junk_total", 0.5))
        ref = float(np.dot(self.embs[f.id], self.ref_emb)) if self.ref_emb is not None and f.id in self.embs else 0.0
        needs = 0.15 if f.cand.source in SEARCH_SOURCES | CROWD_SOURCES else 0.0
        # a post whose caption names the university and the theme is looked at before one that only matched a query
        rel = 0.2 * min(1.0, (f.cand.relevance or 0.0) / 5.0)
        return photo_like + 0.3 * max(0.0, ref - 0.6) + needs + rel

    def describe_for_ai(self, f: Fetched) -> str:
        """One metadata line per candidate for the inspector prompt."""
        c = f.cand
        cap = " ".join((c.title or c.text or "").split())[:90]
        u = urlparse(c.page_url)
        page = (u.netloc.replace("www.", "") + u.path)[:70]
        lat, lon = (c.lat, c.lon) if c.lat is not None else (f.exif_lat, f.exif_lon)
        if lat is None or lon is None:
            geo = "none"
        else:
            d = self.vctx.geom.distance_m(lat, lon)
            geo = ("inside the campus outline" if self.vctx.geom.mode == "polygon" else "within 500 m of the university point") \
                if d == 0 else f"{d:.0f} m from the campus"
        kind = "city article/category (photo of the city)" if c.is_city or c.source in CITY_SOURCES else SOURCE_LABELS.get(c.source, c.source)
        found = f"; found by image search {c.collector.split(':', 1)[1]!r}" if (c.collector or "").startswith("google:") \
            else f"; found by searching {c.query!r}" if c.query else ""
        return (f"source={kind}{found}; caption={cap!r}; page={page}; geotag={geo}; size={f.width}x{f.height}; "
                f"date={c.date or f.exif_date or '?'}")

    async def load_reference(self) -> None:
        """A trusted photo of the main building (Wikidata P18, else the Wikipedia lead image) for the inspector."""
        img = None
        for url in [self.uni.image_url]:
            if not url:
                continue
            try:
                r = await http.get(url, timeout=4.0)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    im = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if min(im.size) >= 150:
                        emb = await vision.encode([im])
                        clf = vision.clip.classify(emb)[0]
                        if clf["junk_total"] < 0.5:  # P18 is sometimes a logo or a map
                            img, self.ref_emb = im, emb[0]
                            self.reference = {"url": url}
            except Exception as e:  # noqa: BLE001
                self.logf(f"reference photo failed: {e!r}")
            if img is not None:
                break
        self.logf(f"reference photo: {'yes' if img is not None else 'none'}")
        if self.inspector:
            self.inspector.set_reference(img)

    async def embed(self, items: list[Fetched]) -> None:
        new = [f for f in items if f.id not in self.embs]
        if not new:
            return
        embs = await vision.encode([f.image for f in new])
        for f, e, c in zip(new, embs, vision.clip.classify(embs)):
            self.embs[f.id] = e
            self.clf[f.id] = c

    # ---------- campus ----------
    async def resolve_campus(self) -> None:
        await self.stage("campus", "running")
        try:
            c = await asyncio.wait_for(enrich_mod.campus(self.uni, self.logf), timeout=settings.campus_budget_s)
        except asyncio.TimeoutError:
            self.logf("campus lookup exceeded its budget, staying in radius mode")
            await self.stage("campus", "skipped", detail="Overpass не ответил вовремя, режим радиуса 500 м")
            return
        except Exception as e:  # noqa: BLE001
            self.logf(f"campus lookup failed: {e!r}")
            await self.stage("campus", "error", detail="режим радиуса 500 м")
            return
        self.campus = c
        self.vctx.geom = CampusGeom(c.polygon, (self.uni.lat, self.uni.lon), c.radius_m)
        await self.stage("campus", "done", detail=f"{'полигон' if c.mode == 'polygon' else 'радиус'}, объектов: {len(c.buildings)}",
                         count=len(c.buildings))
        await self.emit({"type": "campus", "campus": c.model_dump(), "university": self.uni.model_dump(), "elapsed_ms": self.ms()})

    async def after_official(self, network: str, factory):
        """Social sources need the links from the official homepage first; no link → honest empty result."""
        try:
            await asyncio.wait_for(self.official_done.wait(), timeout=9.0)
        except asyncio.TimeoutError:
            pass
        url = self.uni.social.get(network)
        if not url:
            self.logf(f"{network}: no link on the official site")
            return []
        return await factory(url)

    async def official_then(self, coro):
        try:
            return await coro
        finally:
            self.official_done.set()

    async def after_campus(self, factory):
        if self.campus_task:
            try:
                await asyncio.wait_for(asyncio.shield(self.campus_task), timeout=settings.campus_budget_s + 0.5)
            except Exception:
                pass
        return await factory()

    # ---------- source pipelines ----------
    async def source_pipeline(self, name: str, coro, limit: int, timeout: float | None = None) -> None:
        t = time.monotonic()
        # the official site is the richest source and the slowest (homepage + 8 subpages): it gets 4 s more
        timeout = timeout or (settings.source_timeout_s + (settings.campus_budget_s if name in GEO_SOURCES else 0)
                   + (9.0 if name in SOCIAL_SOURCES else 0) + (4.0 if name == "official" else 0)
                   + (6.0 if name in ("web_image", "map_review", "youtube_search") else 0)
                   # these open the videos themselves: a download plus two ffmpeg seeks per clip
                   + (10.0 if name in ("tiktok", "tiktok_search", "tiktok_hashtag", "tiktok_top",
                                       "instagram_search", "instagram_accounts") else 0))
        # searches that fan out over many queries and clips read this and hand back what they have a little before
        # it, so a slow one still makes the first profile; in the background pass nothing is due and they run out
        token = video_frames.DEADLINE.set(None if self.quiet else t + timeout - 1.0)
        try:
            # with the background pass on, a source is never cut off: past its timeout it lands in a later profile
            cands: list[PhotoCandidate] = await (coro if self.deep else asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError:
            await self.source_event(name, "skipped", detail="таймаут источника", ms=int((time.monotonic() - t) * 1000))
            return
        except Exception as e:  # noqa: BLE001
            self.logf(f"{name} failed: {e!r}")
            await self.source_event(name, "error", detail=f"{type(e).__name__}", ms=int((time.monotonic() - t) * 1000))
            return
        finally:
            video_frames.DEADLINE.reset(token)
        # a second run of a source (background pass, retry) only downloads what the first one did not
        cands = [c for c in cands if (name, fetch_id(c.url)) not in self.have]
        if not cands:
            empty = "ничего не найдено"
            if name in ("web_image", "map_review") and http.serper_out():
                empty = "закончились кредиты serper.dev"
            await self.source_event(name, "done", count=self.got.get(name, 0), ms=int((time.monotonic() - t) * 1000),
                                    detail=empty if not self.got.get(name) else None)
            return
        await self.source_event(name, "fetching", count=len(cands), ms=int((time.monotonic() - t) * 1000))
        # downloads still running when the first profile is due are not dropped: they land in a later one
        late: list[asyncio.Task] = []
        try:
            async def wave(batch: list[Fetched]) -> None:
                await self.ingest(name, batch)

            # images are embedded and queued for the inspector as they arrive, not when the slowest one is in
            await fetch_waves(cands, deadline=self.collect_deadline, on_batch=wave, limit=limit,
                              late=late if self.deep else None)
            await self.source_event(name, "done", count=self.got.get(name, 0), ms=int((time.monotonic() - t) * 1000),
                                    detail=f"кандидатов {len(cands)}")
            if late:
                rest = [r for r in await asyncio.gather(*late, return_exceptions=True) if isinstance(r, Fetched)]
                await self.ingest(name, rest)
                await self.source_event(name, "done", count=self.got.get(name, 0),
                                        ms=int((time.monotonic() - t) * 1000), detail=f"кандидатов {len(cands)}")
        finally:
            for x in late:
                x.cancel()

    async def ingest(self, name: str, fetched: list[Fetched]) -> None:
        """New images of one source: embedded, queued for the inspector, shown as preliminary before the first profile."""
        fetched = [f for f in fetched if (name, f.id) not in self.have]
        self.logf(f"{name}: +{len(fetched)} usable images")
        if not fetched:
            return
        self.have.update((name, f.id) for f in fetched)
        self.got[name] = self.got.get(name, 0) + len(fetched)
        await self.embed(fetched)
        self.fetched.extend(fetched)
        if self.inspector:
            # only near-certain junk skips the inspector: CLIP calls fountains "maps" and facades "posters"
            todo = [f for f in fetched if self.clf[f.id]["junk_total"] < 0.97]
            if name in ("mapillary", "flickr"):
                todo = todo[:settings.inspect_max_street]
            self.inspector.submit(todo, self.ai_priority)
        if self.quiet:  # the next profile carries them
            return
        prelim = self.analyze(fetched)
        for p in prelim:
            p.preliminary = True
        await self.emit({"type": "photos", "source": name, "preliminary": True,
                         "photos": [p.model_dump() for p in prelim if not p.rejected],
                         "rejected": len([p for p in prelim if p.rejected]), "elapsed_ms": self.ms()})

    async def commons_bundle(self, kind: str) -> list[PhotoCandidate]:
        uni = self.uni
        if kind == "commons_cat":
            titles = await commons.category_files(uni.commons_category, 60) if uni.commons_category else []
        elif kind == "commons_depicts":
            titles = await commons.depicts_files(uni.qid, 40)
        elif kind == "wikipedia":
            lists = await asyncio.gather(*[wikipedia.article_images(l, t) for l, t in uni.wikipedia.items()])
            titles = list(dict.fromkeys(t for l in lists for t in l))
        else:
            return []
        if not titles:
            return []
        infos = await commons.image_info(titles[:60])
        return commons.to_candidates(infos, kind)

    async def commons_geo(self) -> list[PhotoCandidate]:
        geom = self.vctx.geom
        b = geom.bbox()
        radius = int(max(400, min(3000, 0.55 * 111_000 * max(b[2] - b[0], (b[3] - b[1]) * 0.7))))
        hits = await commons.geosearch_files(self.uni.lat, self.uni.lon, radius, 60)
        inside = [h for h in hits if geom.distance_m(h["lat"], h["lon"]) <= 150]
        if not inside:
            return []
        infos = await commons.image_info([h["title"] for h in inside[:40]])
        return commons.to_candidates(infos, "commons_geo", geo_hints={h["title"]: (h["lat"], h["lon"]) for h in inside})

    async def city_bundle(self) -> list[PhotoCandidate]:
        uni = self.uni
        titles: list[str] = []
        lists = await asyncio.gather(*[wikipedia.article_images(l, t, 25) for l, t in uni.city_wikipedia.items()])
        for l in lists:
            titles += l
        by_source = {t: "city_article" for t in titles}
        if uni.city_commons_category:
            for t in await commons.category_files(uni.city_commons_category, 20):
                by_source.setdefault(t, "city_cat")
        titles = list(by_source)[:30]
        if not titles:
            return []
        infos = await commons.image_info(titles)
        out = []
        for t, info in infos.items():
            out += commons.to_candidates({t: info}, by_source.get(t, "city_article"), is_city=True)
        return out[:14]

    # ---------- main ----------
    async def run(self) -> Profile:
        await self.stage("facts", "running")
        self.uni, _wiki = await enrich_mod.facts(self.qid, self.logf)
        self.plan = await search_plan.for_university(self.qid)
        self.campus = enrich_mod.provisional_campus(self.uni)
        flags = await cache.get_flags(self.qid)
        self.vctx = VerifyContext(
            geom=CampusGeom(None, (self.uni.lat, self.uni.lon), 500.0), aliases=self.uni.aliases,
            city_center=(self.uni.city_lat, self.uni.city_lon) if self.uni.city_lat is not None else None, flags=flags)
        await self.stage("facts", "done", detail=f"{self.uni.city or ''}, координаты: {self.uni.coord_source}")
        await self.emit({"type": "university", "university": self.uni.model_dump(), "campus": self.campus.model_dump(),
                         "elapsed_ms": self.ms()})

        self.campus_task = asyncio.create_task(self.resolve_campus())
        asyncio.create_task(crossdup.refresh())   # every other profile's photos, for the stock check
        self.context_task = asyncio.create_task(context_mod.build(self.uni, self.campus))
        asyncio.create_task(vision.warmup())
        if settings.active_llm() != "none":
            self.inspector = Inspector(self.uni, self.describe_for_ai)
        ref_task = asyncio.create_task(self.load_reference())

        await self.stage("collect", "running")
        await self.stage("fetch", "running")
        per = settings.max_per_source
        enabled = {n for n, s in settings.sources_status().items() if s["enabled"]}
        for name, st in settings.sources_status().items():
            if name in ("mapillary", "flickr", "places", "vk", "vk_geo", "web_image", "map_review", "instagram",
                        "instagram_tagged", "instagram_search", "tiktok", "tiktok_search", "tiktok_hashtag",
                        "tiktok_top",
                        "youtube_search") and not st["enabled"]:
                await self.source_event(name, "disabled", detail=f"нет ключа {st.get('env')}")

        def bbox_pad() -> list[float]:
            return self.vctx.geom.bbox(pad_m=120)

        self.collect_deadline = self.deadline - (6.0 if self.inspector else 1.5)
        self.official_done = asyncio.Event()
        factories: dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]] = {
            "official": (lambda: self.official_then(official_site.collect(self.uni)), per),
            "telegram": (lambda: self.after_official("telegram", social.telegram), 20),
            "youtube": (lambda: self.after_official("youtube", social.youtube), 12),
            "commons_cat": (lambda: self.commons_bundle("commons_cat"), per),
            "commons_depicts": (lambda: self.commons_bundle("commons_depicts"), 20),
            "wikipedia": (lambda: self.commons_bundle("wikipedia"), 20),
            "city_article": (lambda: self.city_bundle(), 14),
            "commons_geo": (lambda: self.after_campus(self.commons_geo), 20),
            "commons_search": (lambda: web_images.commons_search(self.uni), 15),
            "openverse": (lambda: web_images.openverse(self.uni), 10),
        }
        if "web_image" in enabled:
            factories["web_image"] = (lambda: web_images.google_images(self.uni), 45)
            factories["map_review"] = (lambda: map_reviews.collect(self.uni), 24)
        if "instagram" in enabled:
            factories["instagram"] = (lambda: self.after_official("instagram", social_api.instagram_posts), 20)
            factories["instagram_tagged"] = (lambda: self.after_official("instagram", social_api.instagram_tagged), 20)
            factories["tiktok"] = (lambda: self.after_official("tiktok", social_api.tiktok_videos), 10)
            # the themes of the search plan (atmosphere, a day in the life, campus, dorms…) searched the way students
            # post them; the first profile gets the fast themes, the background pass all of them
            factories["tiktok_search"] = (lambda: social_search.tiktok(self.uni, self.plan, fast=True), 80)
            factories["instagram_search"] = (lambda: social_search.instagram(self.uni, self.plan, fast=True), 60)
            factories["instagram_accounts"] = (
                lambda: social_search.instagram_accounts(self.uni, self.plan, fast=True), 40)
        if "youtube_search" in enabled:
            factories["youtube_search"] = (lambda: social_api.youtube_intents(self.uni, self.plan), 30)
        if "mapillary" in enabled:
            factories["mapillary"] = (lambda: self.after_campus(lambda: mapillary.collect(bbox_pad(), 20)), 15)
        if "flickr" in enabled:
            factories["flickr"] = (lambda: self.after_campus(lambda: flickr.collect(bbox_pad(), 30)), 30)
        if "places" in enabled:
            # the university's pin and its dorms, library, sports complex, canteen as their own pins on Google Maps
            factories["places"] = (lambda: places.collect(self.uni, self.plan), 60)
        if "vk" in enabled:
            factories["vk"] = (lambda: self.after_official("vk", social.vk), 30)
            # geotagged photos of passers-by and students: the anchor is the coordinate, not the university's channel
            factories["vk_geo"] = (lambda: vk_geo.collect(self.uni), 24)
        external = await cache.get_external(self.qid)
        if external:
            ext_cands = [PhotoCandidate.model_validate(c) for c in external]
            factories["external"] = (lambda: asyncio.sleep(0, result=ext_cands), per)
            self.logf(f"external collector supplied {len(ext_cands)} candidates")

        # CLIP loads lazily inside embed(); at server start it is already warm, so collection begins immediately
        self.factories = factories
        tasks = {asyncio.create_task(self.source_pipeline(n, f(), lim), name=n): n for n, (f, lim) in factories.items()}
        self.first_tasks = {n: t for t, n in tasks.items()}
        # the first profile takes what has downloaded by collect_deadline (the budget minus a reserve for the inspector
        # and the assembly); sources get 2 s more to embed and hand their images over. Without the background pass the
        # rest is cancelled; with it, it keeps running and lands in a later profile
        done, pending = await asyncio.wait(tasks.keys(), timeout=max(self.collect_deadline + 2.0 - time.monotonic(), 3.0))
        if not self.deep:
            for t in pending:
                t.cancel()
                self.partial = True
                await self.source_event(tasks[t], "skipped", detail="не уложился в бюджет времени")
        for t in done:
            if t.exception():
                self.logf(f"{tasks[t]} crashed: {t.exception()!r}")
        try:
            await asyncio.wait_for(asyncio.shield(self.campus_task), timeout=1.0)
        except Exception:
            pass
        await self.stage("collect", "done", count=sum(s.get("count", 0) for s in self.sources_status.values() if s["status"] == "done"))
        await self.stage("fetch", "done", count=len(self.fetched))

        # ---------- AI inspector: wait for the batches still in flight ----------
        try:
            await asyncio.wait_for(ref_task, timeout=1.0)
        except Exception:
            pass
        if self.inspector:
            await self.stage("inspect", "running")
            # with the background pass on, the workers keep judging after this: the first profile takes what is done
            await self.inspector.finish(timeout=max(2.0, self.until(self.cap_s - 3.5)), stop=not self.deep)
            st = self.inspector.stats()
            self.logf(f"inspector {st['model']}: {st['photos']}/{len(self.fetched)} photos judged "
                      f"({st['cached']} from cache, {st['calls']} calls, {st['tokens_in']}+{st['tokens_out']} tokens, "
                      f"{st['errors']} errors)")
            await self.stage("inspect", "done" if st["photos"] else "error", count=st["photos"],
                             detail=self.inspect_detail())
        else:
            await self.stage("inspect", "skipped", detail="нет ключа LLM: только CLIP и сигналы источников")

        profile = await self.finalize(final=not self.deep)
        if self.deep:
            try:
                await self.background()
            except Exception as e:  # noqa: BLE001
                self.logf(f"background pass failed: {e!r}")
            profile = await self.finalize(final=True)
        return profile

    def inspect_detail(self) -> str:
        st = self.inspector.stats()
        models = " + ".join(st["models"]) or st["model"]
        return (f"проверено ИИ: {st['photos']} из {len(self.fetched)} · {models}"
                # said out loud: the photos it could not look at are rejected, not trusted
                + (" · суточный лимит ИИ исчерпан, непроверенные фото из поиска не показаны"
                   if st["quota_out"] else f" · ошибок: {st['errors']}" if st["errors"] else ""))

    # ---------- assembly (the first profile, the background updates, the last one) ----------
    async def finalize(self, final: bool = True) -> Profile:
        if final:
            self.final_started = True
        # ---------- analysis over the full set (cross-source signals need everything) ----------
        await self.stage("analyze", "running")
        reps = dedup.merge_exact(self.fetched)
        photos = self.analyze(reps)
        good = [p for p in photos if not p.rejected]
        rejected = [p for p in photos if p.rejected]
        kept = dedup.cluster_similar(good, self.embs)
        hidden = len(good) - len(kept)
        cover_ids = await self.cover_for(kept, final)   # first: its ratings feed the album order and the collage
        kept = curate.feature(kept, self.embs)
        collage = collage_mod.build(kept, self.plan)
        rejected.sort(key=lambda p: -p.confidence)
        await self.stage("analyze", "done",
                         detail=f"копий объединено: {len(self.fetched) - len(reps)}, похожих скрыто: {hidden}, "
                                f"отклонено: {len(rejected)}, в подборке: {sum(p.featured for p in kept)}")

        # ---------- assemble ----------
        await self.stage("assemble", "running")
        cats: dict[str, CategoryStats] = {c: CategoryStats() for c in CATEGORIES}
        for p in kept:
            cats[p.category].verified += p.level == "verified"
            cats[p.category].likely += p.level == "likely"
        for p in rejected:
            cats[p.category].rejected += 1
        checked = [n for n, s in self.sources_status.items() if s["status"] == "done"]
        coverage: dict[str, str] = {}
        for c, s in cats.items():
            s.sources_checked = checked
            s.coverage = ("strong" if s.verified >= 3 else "medium" if s.verified + s.likely >= 2
                          else "weak" if s.verified + s.likely >= 1 else "none")
            coverage[c] = s.coverage
        n_verified = sum(s.verified for s in cats.values())
        coverage["overall"] = "strong" if n_verified >= 12 else "medium" if n_verified >= 5 else "weak"
        timeline: dict[str, int] = {}
        for p in kept:
            if p.year:
                timeline[str(p.year)] = timeline.get(str(p.year), 0) + 1
        # the walk is street-level context: every real Mapillary frame, even the ones not chosen for the albums
        walk = sorted([p for p in kept + rejected if p.source == "mapillary"
                       and (not p.rejected or (p.reject_reason or "").startswith(("низкая уверенность", "малоинформативный")))],
                      key=lambda p: p.date or "")
        if self.context is None:
            # the first profile waits a little; the background updates take it once it is there; the last one waits
            wait = max(0.3, self.until(self.cap_s - 3.0)) if not self.quiet else \
                max(0.0, self.guard_end - time.monotonic()) if final else 0.0
            try:  # shielded: a lookup that misses the first profile keeps going for the next one
                self.context = await asyncio.wait_for(asyncio.shield(self.context_task), timeout=wait)
            except Exception:
                self.context = None
        context = self.context
        stats = {"verified": n_verified, "likely": sum(s.likely for s in cats.values()),
                 "sources": len({s for p in kept for s in p.sources}),
                 "weak": [c for c in CATEGORIES if coverage[c] in ("weak", "none") and c != "city"],
                 "per_category": {c: {"verified": s.verified, "likely": s.likely} for c, s in cats.items()}}
        if self.description is None:
            self.description = await describe.build(self.uni, self.campus, stats, context.model_dump() if context else None,
                                                    timeout=min(6.0, self.until(self.cap_s - 0.7)))
        elif self.quiet and final:
            # written again over everything that was found, with time to answer; a model that fails now does not
            # replace its own earlier text with the template
            d = await describe.build(self.uni, self.campus, stats, context.model_dump() if context else None, timeout=20.0)
            if d.mode == "llm" or self.description.mode != "llm":
                self.description = d
        description = self.description
        # the time to the first answer: that is what the page waited for; the background pass after it is not
        elapsed = self.first_ms if self.first_ms is not None else self.ms()
        profile = Profile(
            university=self.uni, campus=self.campus, photos=kept, rejected=rejected, categories=cats,
            coverage=coverage, description=description, context=context, walk=walk,
            timeline=dict(sorted(timeline.items())), stages=list(self.stages.values()),
            sources_status=self.sources_status, log=self.log,
            generated_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=elapsed, partial=self.partial,
            reference=self.reference, inspector=self.inspector.stats() if self.inspector else None,
            collage=collage, plan=collage_mod.plan_summary(self.plan, self.uni), cover=cover_ids,
        )
        await self.stage("assemble", "done", count=len(kept))
        profile.stages = list(self.stages.values())
        if self.first_ms is None:
            self.first_ms = profile.elapsed_ms = self.ms()
        await cache.save_profile(profile)
        self.logf(f"profile {'ready' if not self.quiet else 'updated'}: {len(kept)} photos, {len(rejected)} rejected, "
                  f"{self.ms()} ms")
        await self.emit({"type": "profile", "profile": profile.model_dump(), "final": final,
                         "elapsed_ms": profile.elapsed_ms})
        return profile

    # ---------- the cover ----------
    async def cover_for(self, kept: list[Photo], final: bool) -> list[str]:
        """The photos the page opens with (pipeline/cover.py). The editor is asked in the background - the first
        profile never waits for it, the next update carries its choice - once for the first profile's candidates and
        once more for the last profile's if they changed; the last profile waits up to 30 s for that answer."""
        cands = cover_mod.candidates(kept, self.embs)
        k = cover_mod.key(self.uni.qid, cands)
        idle = self.cover_task is None or self.cover_task.done()
        if k and k != self.cover_key and idle:
            hit = await cover_mod.cached(k)
            if hit:
                self.cover_pick, self.cover_key = hit, k
            elif final or self.cover_pick is None:
                self.cover_task = asyncio.create_task(self.cover_later(cands, k))
        if final and self.cover_task and not self.cover_task.done():
            await asyncio.wait({self.cover_task}, timeout=30.0)
        return cover_mod.apply(self.cover_pick, kept)

    async def cover_later(self, cands: list[Photo], k: str) -> None:
        res = await cover_mod.pick(self.uni, cands, k)
        if not res:
            return
        self.cover_pick, self.cover_key = res, k
        self.logf(f"cover: {len(cands)} candidates -> {len(res.order)} picked ({res.model})")
        if not self.final_started and self.quiet:
            try:   # the open page gets it now, not at the next background update
                await self.finalize(final=False)
            except Exception as e:  # noqa: BLE001
                self.logf(f"cover update failed: {e!r}")
        elif self.final_started:
            # the last profile went out without it (the editor took longer than the wait): into the saved one
            p = await cache.get_profile(self.uni.qid)
            if p:
                p.cover = cover_mod.apply(res, p.photos)
                p.cached = False
                await cache.save_profile(p)

    # ---------- background pass ----------
    async def background(self) -> None:
        """Everything the first answer had to leave on the table, with no clock on it.

        The first profile is on screen and saved, so from here nothing is due: the sources still running finish, the
        downloads still in flight land, the social networks are searched in full (every place query in both
        languages, more pages, frames from more clips, the university's whole Instagram grid and every post that
        tags it), a source that failed gets a second try, the campus outline is asked for again if Overpass was
        slow, and the inspector judges everything it has not seen. Silently: no stage bar, no timer - the open page
        gets the grown profile every background_refresh_s while something new comes in, and the last one at the end.
        The only limit is background_guard_s against something hanging forever. Nothing is prepared beforehand -
        this is still the same request, it just keeps going after the first answer."""
        self.quiet = True
        self.deadline = self.collect_deadline = math.inf
        self.guard_end = time.monotonic() + settings.background_guard_s
        enabled = {n for n, st in settings.sources_status().items() if st["enabled"]}
        if self.inspector:
            # photos that hit the first pass's cap were left without a verdict, and a search hit without one is not
            # shown at all: with the cap raised they get their look now
            self.inspector.cap = settings.inspect_max_photos_deep
            self.inspector.submit([f for f in self.fetched
                                   if f.id not in self.inspector.verdicts and self.clf[f.id]["junk_total"] < 0.97],
                                  self.ai_priority)
            self.inspector._kick()   # what the first pass left in the queue continues from where it stopped
        factories = self.deep_factories(enabled)
        for n, st in list(self.sources_status.items()):
            if st["status"] in ("error", "skipped") and n in self.factories and n not in factories:
                factories[n] = self.factories[n]   # one more try: most failures are a slow or busy server
        tasks = {t for t in self.first_tasks.values() if not t.done()}
        tasks |= {asyncio.create_task(self.source_pipeline(n, self.after_first(n, f), lim), name=f"bg:{n}")
                  for n, (f, lim) in factories.items()}
        if self.stages["campus"].status in ("skipped", "error"):
            tasks.add(asyncio.create_task(self.late_campus()))
        ticker = asyncio.create_task(self.refresh_loop())
        try:
            _, pending = await asyncio.wait(tasks, timeout=max(1.0, self.guard_end - time.monotonic())) \
                if tasks else (set(), set())
            for t in pending:
                t.cancel()
            if pending:
                self.partial = True
                self.logf(f"background guard: stopped {len(pending)} sources that never finished")
            if self.inspector:
                await self.inspector.finish(timeout=max(1.0, self.guard_end - time.monotonic()))
                if self.inspector.queue and not self.inspector.quota_out:
                    self.partial = True
                st = self.inspector.stats()
                # the details panel shows the whole count, not the first profile's
                self.stages["inspect"].count, self.stages["inspect"].detail = st["photos"], self.inspect_detail()
                self.logf(f"background pass: inspector judged {st['photos']} photos in total, "
                          f"{len(self.fetched)} images collected")
                try:
                    learned = await search_learn.record(self.uni.qid, search_learn.mine(self.fetched, self.inspector.verdicts))
                    tags = search_learn.proven_tags(learned, await search_learn.generic_tags(), n=5, uni=self.uni,
                                                    forms=search_plan.name_forms(self.uni),
                                                    places=await search_learn.places_of(self.uni))
                    self.logf(f"search learned: {len(learned.tags)} tags, {len(learned.authors)} authors, "
                              f"proven tags {tags}")
                except Exception as e:  # noqa: BLE001
                    self.logf(f"search learning failed: {e!r}")
        finally:
            self.bg_done.set()
            await ticker

    def deep_factories(self, enabled: set[str]) -> dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]]:
        """The full versions of the searches the first pass ran in short."""
        v, n = settings.deep_videos, settings.deep_per_source
        factories: dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]] = {}
        if "web_image" in enabled:
            factories["web_image"] = (self.google_deep, 3 * n)
            # the dorms, the library, the sports complex, the canteen as their own pins on Google Maps
            factories["map_review"] = (lambda: map_reviews.collect_intents(self.uni, self.plan), 3 * n)
        if "instagram" not in enabled:   # the social networks below all go through ScrapeCreators
            return factories
        factories |= {
            "tiktok_search": (lambda: social_search.tiktok(self.uni, self.plan), 4 * n),
            "instagram_search": (lambda: social_search.instagram(self.uni, self.plan), 3 * n),
            "instagram_accounts": (lambda: social_search.instagram_accounts(self.uni, self.plan), 2 * n),
            "social_learned": (self.learned_round, 3 * n),
        }
        ig = self.uni.social.get("instagram")
        if ig:
            factories["instagram"] = (lambda: social_api.instagram_posts(ig, n), n)
            factories["instagram_tagged"] = (lambda: social_api.instagram_tagged(ig, n), n)
        tt = self.uni.social.get("tiktok")
        if tt:
            factories["tiktok"] = (lambda: social_api.tiktok_videos(tt, n, v), n)
        return factories

    async def learned_round(self) -> list[PhotoCandidate]:
        """The social search's second round (search_learn.py): once the inspector has judged what the first searches
        brought, their proven hashtags and authors are searched in turn."""
        end = time.monotonic() + 90.0
        while time.monotonic() < end:   # the first searches' posts judged (at least 15 s in), or 90 s
            social = [f for f in self.fetched if search_learn._platform(f.cand.source)]
            judged = sum(f.id in self.inspector.verdicts for f in social) if self.inspector else 0
            if social and judged >= 0.8 * len(social) and time.monotonic() > end - 75.0:
                break
            await asyncio.sleep(3.0)
        if not self.inspector:
            return []
        now = search_learn.mine(self.fetched, self.inspector.verdicts)
        searched = {c.query.lstrip("#") for f in self.fetched for c in [f.cand, *f.extra_sources]
                    if c.query and search_learn._platform(c.source)}
        return await social_search.learned_round(self.uni, self.plan, now, searched)

    async def google_deep(self) -> list[PhotoCandidate]:
        """The classic category queries in the local language, and the themes of the search plan."""
        a, b = await asyncio.gather(web_images.google_images(self.uni, deep=True),
                                    web_images.google_intents(self.uni, self.plan), return_exceptions=True)
        return [c for r in (a, b) if isinstance(r, list) for c in r]

    async def after_first(self, name: str, factory):
        """The full run of a source starts when its first-pass run is over: the calls they share are cached by
        then, not paid twice."""
        t = self.first_tasks.get(name)
        if t and not t.done():
            await asyncio.wait({t})
        return await factory()

    async def late_campus(self) -> None:
        """Overpass did not answer in time for the first profile: ask again without the clock. With an outline the
        verification gets its polygon, and the geotagged sources look inside it again."""
        try:
            c = await enrich_mod.campus(self.uni, self.logf)
        except Exception as e:  # noqa: BLE001
            self.logf(f"late campus lookup failed: {e!r}")
            return
        self.campus = c
        self.vctx.geom = CampusGeom(c.polygon, (self.uni.lat, self.uni.lon), c.radius_m)
        st = self.stages["campus"]
        st.status, st.count = "done", len(c.buildings)
        st.detail = f"{'полигон' if c.mode == 'polygon' else 'радиус'}, объектов: {len(c.buildings)} (после первого профиля)"
        if c.mode == "polygon":
            await asyncio.gather(*[self.source_pipeline(n, self.factories[n][0](), self.factories[n][1])
                                   for n in GEO_SOURCES if n in self.factories])

    def progress(self) -> tuple[int, int]:
        return len(self.fetched), len(self.inspector.verdicts) if self.inspector else 0

    async def refresh_loop(self) -> None:
        """The open page gets the grown profile while the background pass runs - only when something is new."""
        seen = self.progress()
        while not self.bg_done.is_set():
            try:
                await asyncio.wait_for(self.bg_done.wait(), timeout=settings.background_refresh_s)
                return   # the last profile is assembled by run()
            except asyncio.TimeoutError:
                pass
            if self.progress() == seen:
                continue
            seen = self.progress()
            try:
                await self.finalize(final=False)
            except Exception as e:  # noqa: BLE001
                self.logf(f"background update failed: {e!r}")


async def run(qid: str, emit: Emit) -> Profile:
    r = Run(qid, emit)
    try:
        return await r.run()
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline failed for %s", qid)
        await emit({"type": "error", "message": f"{type(e).__name__}: {e}", "log": r.log, "elapsed_ms": r.ms()})
        raise
