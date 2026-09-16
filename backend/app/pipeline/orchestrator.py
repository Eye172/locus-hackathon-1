"""Runs the whole pipeline for one university under a time budget and streams events.

Event types: stage, university, campus, source, photos (preliminary batches), profile (final), error.
Design: facts come first (≈1 s) so the UI can render the header; the campus outline (Overpass) is resolved
in parallel with the non-geographic sources; geo sources wait for it. Everything is capped by the budget.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import numpy as np

from .. import cache
from ..config import settings
from ..geo import CampusGeom
from ..models import (BROCHURE_SOURCES, CATEGORIES, Campus, CategoryStats, Photo, PhotoCandidate, Profile,
                      SOURCE_LABELS, Stage, University)
from . import categorize as categorize_mod
from . import context as context_mod
from . import dedup, describe, vision
from . import enrich as enrich_mod
from . import judge as judge_mod
from .fetch import Fetched, fetch_all
from .sources import commons, flickr, mapillary, official_site, places, wikipedia
from .verify import VerifyContext, score

log = logging.getLogger("campuslens.orchestrator")
Emit = Callable[[dict], Awaitable[None]]

STAGES = [
    ("facts", "Факты о вузе"),
    ("campus", "Границы кампуса (OSM)"),
    ("collect", "Сбор кандидатов из источников"),
    ("fetch", "Загрузка изображений"),
    ("analyze", "Проверка, категории, дубли"),
    ("assemble", "Сборка профиля"),
]
GEO_SOURCES = {"commons_geo", "mapillary", "flickr"}


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

    # ---------- helpers ----------
    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def logf(self, s: str) -> None:
        self.log.append(f"[{self.ms():>6} ms] {s}")
        log.info("%s %s", self.qid, s)

    async def stage(self, key: str, status: str, detail: str | None = None, count: int | None = None) -> None:
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
        )

    def text_of(self, f: Fetched) -> str:
        return " ".join([f.cand.text or "", f.cand.title or ""] + [x.text or "" for x in f.extra_sources])

    def analyze(self, items: list[Fetched]) -> list[Photo]:
        photos: list[Photo] = []
        buildings = self.campus.buildings if self.campus else []
        for f in items:
            p = self.make_photo(f)
            categorize_mod.categorize(p, self.clf[f.id], self.text_of(f), f.cand.is_city, buildings)
            score(p, self.vctx, self.text_of(f), f.cand.is_city)
            if not p.rejected and p.level == "unverified":
                p.rejected = True
                p.reject_reason = f"низкая уверенность ({p.confidence:.0%}): недостаточно сигналов принадлежности"
            photos.append(p)
        return photos

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

    async def after_campus(self, factory):
        if self.campus_task:
            try:
                await asyncio.wait_for(asyncio.shield(self.campus_task), timeout=settings.campus_budget_s + 0.5)
            except Exception:
                pass
        return await factory()

    # ---------- source pipelines ----------
    async def source_pipeline(self, name: str, coro, limit: int) -> None:
        t = time.monotonic()
        timeout = settings.source_timeout_s + (settings.campus_budget_s if name in GEO_SOURCES else 0)
        try:
            cands: list[PhotoCandidate] = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            await self.source_event(name, "skipped", detail="таймаут источника", ms=int((time.monotonic() - t) * 1000))
            return
        except Exception as e:  # noqa: BLE001
            self.logf(f"{name} failed: {e!r}")
            await self.source_event(name, "error", detail=f"{type(e).__name__}", ms=int((time.monotonic() - t) * 1000))
            return
        if not cands:
            await self.source_event(name, "done", count=0, ms=int((time.monotonic() - t) * 1000), detail="ничего не найдено")
            return
        await self.source_event(name, "fetching", count=len(cands), ms=int((time.monotonic() - t) * 1000))
        fetch_deadline = max(self.deadline, time.monotonic() + 5.0)
        fetched = await fetch_all(cands, deadline=fetch_deadline, limit=limit)
        self.logf(f"{name}: {len(cands)} candidates -> {len(fetched)} usable images")
        if fetched:
            await self.embed(fetched)
            self.fetched.extend(fetched)
            prelim = self.analyze(fetched)
            for p in prelim:
                p.preliminary = True
            await self.emit({"type": "photos", "source": name, "preliminary": True,
                             "photos": [p.model_dump() for p in prelim if not p.rejected],
                             "rejected": len([p for p in prelim if p.rejected]), "elapsed_ms": self.ms()})
        await self.source_event(name, "done", count=len(fetched), ms=int((time.monotonic() - t) * 1000),
                                detail=f"кандидатов {len(cands)}")

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
        self.campus = enrich_mod.provisional_campus(self.uni)
        flags = await cache.get_flags(self.qid)
        self.vctx = VerifyContext(
            geom=CampusGeom(None, (self.uni.lat, self.uni.lon), 500.0), aliases=self.uni.aliases,
            city_center=(self.uni.city_lat, self.uni.city_lon) if self.uni.city_lat is not None else None, flags=flags)
        await self.stage("facts", "done", detail=f"{self.uni.city or ''}, координаты: {self.uni.coord_source}")
        await self.emit({"type": "university", "university": self.uni.model_dump(), "campus": self.campus.model_dump(),
                         "elapsed_ms": self.ms()})

        self.campus_task = asyncio.create_task(self.resolve_campus())
        context_task = asyncio.create_task(context_mod.build(self.uni, self.campus))
        asyncio.create_task(vision.warmup())

        await self.stage("collect", "running")
        await self.stage("fetch", "running")
        per = settings.max_per_source
        enabled = {n for n, s in settings.sources_status().items() if s["enabled"]}
        for name, st in settings.sources_status().items():
            if name in ("mapillary", "flickr", "places") and not st["enabled"]:
                await self.source_event(name, "disabled", detail=f"нет ключа {st.get('env')}")

        def bbox_pad() -> list[float]:
            return self.vctx.geom.bbox(pad_m=120)

        factories: dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]] = {
            "official": (lambda: official_site.collect(self.uni), per),
            "commons_cat": (lambda: self.commons_bundle("commons_cat"), per),
            "commons_depicts": (lambda: self.commons_bundle("commons_depicts"), 20),
            "wikipedia": (lambda: self.commons_bundle("wikipedia"), 20),
            "city_article": (lambda: self.city_bundle(), 14),
            "commons_geo": (lambda: self.after_campus(self.commons_geo), 30),
        }
        if "mapillary" in enabled:
            factories["mapillary"] = (lambda: self.after_campus(lambda: mapillary.collect(bbox_pad(), 30)), 30)
        if "flickr" in enabled:
            factories["flickr"] = (lambda: self.after_campus(lambda: flickr.collect(bbox_pad(), 30)), 30)
        if "places" in enabled:
            factories["places"] = (lambda: places.collect(self.uni), 10)
        external = await cache.get_external(self.qid)
        if external:
            ext_cands = [PhotoCandidate.model_validate(c) for c in external]
            factories["external"] = (lambda: asyncio.sleep(0, result=ext_cands), per)
            self.logf(f"external collector supplied {len(ext_cands)} candidates")

        # CLIP loads lazily inside embed(); at server start it is already warm, so collection begins immediately
        tasks = {asyncio.create_task(self.source_pipeline(n, f(), lim), name=n): n for n, (f, lim) in factories.items()}
        done, pending = await asyncio.wait(tasks.keys(), timeout=max(self.remaining(), 3.0))
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

        # ---------- analysis over the full set (cross-source signals need everything) ----------
        await self.stage("analyze", "running")
        reps = dedup.merge_exact(self.fetched)
        photos = self.analyze(reps)
        # Judge agent: second opinion on borderline photos (only when an LLM key is configured)
        if settings.active_llm() != "none" and self.remaining() > 2:
            try:
                n = await asyncio.wait_for(judge_mod.run(photos, {f.id: f for f in reps}, self.uni.name, self.uni.city),
                                           timeout=max(3.0, self.remaining()))
                self.logf(f"judge ({settings.active_llm()}): {n} verdicts applied")
            except asyncio.TimeoutError:
                self.logf("judge skipped: budget")
        good = [p for p in photos if not p.rejected]
        rejected = [p for p in photos if p.rejected]
        kept = dedup.cluster_similar(good, self.embs)
        hidden = len(good) - len(kept)
        await self.stage("analyze", "done",
                         detail=f"копий объединено: {len(self.fetched) - len(reps)}, похожих скрыто: {hidden}, отклонено: {len(rejected)}")

        # ---------- assemble ----------
        await self.stage("assemble", "running")
        kept.sort(key=lambda p: (-p.confidence, -(p.width * p.height)))
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
        walk = sorted([p for p in kept if p.source == "mapillary"], key=lambda p: p.date or "")
        try:
            context = await asyncio.wait_for(context_task, timeout=max(1.0, self.remaining() + 4))
        except Exception:
            context = None
        stats = {"verified": n_verified, "likely": sum(s.likely for s in cats.values()),
                 "sources": len({s for p in kept for s in p.sources}),
                 "weak": [c for c in CATEGORIES if coverage[c] in ("weak", "none") and c != "city"],
                 "per_category": {c: {"verified": s.verified, "likely": s.likely} for c, s in cats.items()}}
        description = await describe.build(self.uni, self.campus, stats, context.model_dump() if context else None)
        profile = Profile(
            university=self.uni, campus=self.campus, photos=kept, rejected=rejected, categories=cats,
            coverage=coverage, description=description, context=context, walk=walk,
            timeline=dict(sorted(timeline.items())), stages=list(self.stages.values()),
            sources_status=self.sources_status, log=self.log,
            generated_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=self.ms(), partial=self.partial,
        )
        await self.stage("assemble", "done", count=len(kept))
        profile.stages = list(self.stages.values())
        profile.elapsed_ms = self.ms()
        await cache.save_profile(profile)
        self.logf(f"profile ready: {len(kept)} photos, {len(rejected)} rejected, {self.ms()} ms")
        await self.emit({"type": "profile", "profile": profile.model_dump(), "elapsed_ms": self.ms()})
        return profile


async def run(qid: str, emit: Emit) -> Profile:
    r = Run(qid, emit)
    try:
        return await r.run()
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline failed for %s", qid)
        await emit({"type": "error", "message": f"{type(e).__name__}: {e}", "log": r.log, "elapsed_ms": r.ms()})
        raise
