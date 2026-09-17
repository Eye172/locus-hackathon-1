"""Runs the whole pipeline for one university under a time budget and streams events.

Event types: stage, university, campus, source, photos (preliminary batches), profile (final), error.
Design: facts come first (≈1 s) so the UI can render the header; the campus outline (Overpass) is resolved
in parallel with the non-geographic sources; geo sources wait for it. Everything is capped by the budget.
"""
from __future__ import annotations

import asyncio
import io
import logging
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
from . import curate, dedup, describe, vision
from . import enrich as enrich_mod
from .ai_inspector import Inspector
from .fetch import Fetched, fetch_all
from .fetch import photo_id as fetch_id
from .sources import (social, social_api, commons, flickr, map_reviews, mapillary, official_site, places,
                      vk_geo, web_images, wikipedia)
from .verify import CITY_SOURCES, VerifyContext, hard_flags, score

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
    ("deep", "Полный обход соцсетей"),
]
GEO_SOURCES = {"commons_geo", "mapillary", "flickr"}
HARD_CAP_S = 29.0  # the case asks for a useful profile within 30 s: nothing optional may push past this
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
        self.collect_deadline = self.deadline

    # ---------- helpers ----------
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
            score(p, self.vctx, self.text_of(f), is_city, verdict=v, ref_sim=sim)
            photos.append(p)
        return photos

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
        found = f"; found by image search {c.collector.split(':', 1)[1]!r}" if (c.collector or "").startswith("google:") else ""
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
    async def source_pipeline(self, name: str, coro, limit: int) -> None:
        t = time.monotonic()
        # the official site is the richest source and the slowest (homepage + 8 subpages): it gets 4 s more
        timeout = (settings.source_timeout_s + (settings.campus_budget_s if name in GEO_SOURCES else 0)
                   + (9.0 if name in SOCIAL_SOURCES else 0) + (4.0 if name == "official" else 0)
                   + (6.0 if name in ("web_image", "map_review", "youtube_search") else 0)
                   # these open the videos themselves: a download plus two ffmpeg seeks per clip
                   + (10.0 if name in ("tiktok", "tiktok_search", "tiktok_hashtag") else 0))
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
        fetch_deadline = self.collect_deadline
        fetched = await fetch_all(cands, deadline=fetch_deadline, limit=limit)
        self.logf(f"{name}: {len(cands)} candidates -> {len(fetched)} usable images")
        if fetched:
            await self.embed(fetched)
            self.fetched.extend(fetched)
            if self.inspector:
                # only near-certain junk skips the inspector: CLIP calls fountains "maps" and facades "posters"
                todo = [f for f in fetched if self.clf[f.id]["junk_total"] < 0.97]
                if name in ("mapillary", "flickr"):
                    todo = todo[:settings.inspect_max_street]
                self.inspector.submit(todo)
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
                        "instagram_tagged", "tiktok", "tiktok_search", "tiktok_hashtag",
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
            factories["tiktok_search"] = (lambda: social_api.tiktok_search(self.uni), 10)
            factories["tiktok_hashtag"] = (lambda: social_api.tiktok_hashtag(self.uni), 12)
        if "youtube_search" in enabled:
            factories["youtube_search"] = (lambda: social_api.youtube_search(self.uni), 8)
        if "mapillary" in enabled:
            factories["mapillary"] = (lambda: self.after_campus(lambda: mapillary.collect(bbox_pad(), 20)), 15)
        if "flickr" in enabled:
            factories["flickr"] = (lambda: self.after_campus(lambda: flickr.collect(bbox_pad(), 30)), 30)
        if "places" in enabled:
            factories["places"] = (lambda: places.collect(self.uni), 10)
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
        tasks = {asyncio.create_task(self.source_pipeline(n, f(), lim), name=n): n for n, (f, lim) in factories.items()}
        # downloads stop at collect_deadline (the budget minus a reserve for the inspector and the assembly) and keep
        # what has arrived; sources then get 2 s to embed and hand their images over before they are cancelled
        done, pending = await asyncio.wait(tasks.keys(), timeout=max(self.collect_deadline + 2.0 - time.monotonic(), 3.0))
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
            await self.inspector.finish(timeout=max(2.0, self.until(HARD_CAP_S - 3.5)))
            st = self.inspector.stats()
            self.logf(f"inspector {st['model']}: {st['photos']}/{len(self.fetched)} photos judged "
                      f"({st['cached']} from cache, {st['calls']} calls, {st['tokens_in']}+{st['tokens_out']} tokens, "
                      f"{st['errors']} errors)")
            status = "done" if st["photos"] else "error"
            await self.stage("inspect", status, count=st["photos"],
                             detail=f"проверено ИИ: {st['photos']} из {len(self.fetched)} · {st['model']}"
                                    + (f" · ошибок: {st['errors']}" if st["errors"] else ""))
        else:
            await self.stage("inspect", "skipped", detail="нет ключа LLM: только CLIP и сигналы источников")

        profile = await self.finalize(final=not self.deep)
        if self.deep:
            try:
                await self.deepen()
            except Exception as e:  # noqa: BLE001
                self.logf(f"deep pass failed: {e!r}")
                await self.stage("deep", "error", detail=f"{type(e).__name__}")
            profile = await self.finalize(final=True)
        return profile

    # ---------- assembly (runs once for the fast profile, once more after the deep pass) ----------
    async def finalize(self, final: bool = True) -> Profile:
        # ---------- analysis over the full set (cross-source signals need everything) ----------
        await self.stage("analyze", "running")
        reps = dedup.merge_exact(self.fetched)
        photos = self.analyze(reps)
        good = [p for p in photos if not p.rejected]
        rejected = [p for p in photos if p.rejected]
        kept = dedup.cluster_similar(good, self.embs)
        hidden = len(good) - len(kept)
        kept = curate.feature(kept, self.embs)
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
            try:
                self.context = await asyncio.wait_for(self.context_task, timeout=max(0.3, self.until(HARD_CAP_S - 3.0)))
            except Exception:
                self.context = None
        context = self.context
        stats = {"verified": n_verified, "likely": sum(s.likely for s in cats.values()),
                 "sources": len({s for p in kept for s in p.sources}),
                 "weak": [c for c in CATEGORIES if coverage[c] in ("weak", "none") and c != "city"],
                 "per_category": {c: {"verified": s.verified, "likely": s.likely} for c, s in cats.items()}}
        if self.description is None:
            self.description = await describe.build(self.uni, self.campus, stats, context.model_dump() if context else None,
                                                    timeout=min(6.0, self.until(HARD_CAP_S - 0.7)))
        description = self.description
        profile = Profile(
            university=self.uni, campus=self.campus, photos=kept, rejected=rejected, categories=cats,
            coverage=coverage, description=description, context=context, walk=walk,
            timeline=dict(sorted(timeline.items())), stages=list(self.stages.values()),
            sources_status=self.sources_status, log=self.log,
            generated_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=self.ms(), partial=self.partial,
            reference=self.reference, inspector=self.inspector.stats() if self.inspector else None,
        )
        await self.stage("assemble", "done", count=len(kept))
        profile.stages = list(self.stages.values())
        profile.elapsed_ms = self.ms()
        await cache.save_profile(profile)
        self.logf(f"profile ready: {len(kept)} photos, {len(rejected)} rejected, {self.ms()} ms")
        await self.emit({"type": "profile", "profile": profile.model_dump(), "final": final, "elapsed_ms": self.ms()})
        return profile

    # ---------- deep pass ----------
    async def deepen(self) -> None:
        """Everything the 25-second budget had to leave on the table.

        The fast profile is already on screen, so from here the limits come off: every slideshow photo TikTok's own
        search returns, frames from many more clips, the university's whole Instagram grid and every post that tags
        it. New photos stream into the open page as they pass the inspector, and the profile is rebuilt at the end.
        Nothing is prepared beforehand - this is still the same request, it just keeps going after the first answer."""
        if "instagram" not in {n for n, st in settings.sources_status().items() if st["enabled"]}:
            await self.stage("deep", "skipped", detail="нет ключа для соцсетей")
            return
        await self.stage("deep", "running")
        self.deadline = time.monotonic() + settings.deep_budget_s
        self.collect_deadline = self.deadline - 6.0
        if self.inspector:
            # photos that hit the fast pass's cap were dropped without a verdict, and a search hit without one is
            # not shown at all: with the cap lifted they get their look now
            self.inspector.cap = settings.inspect_max_photos_deep
            self.inspector.submit([f for f in self.fetched
                                   if f.id not in self.inspector.verdicts and self.clf[f.id]["junk_total"] < 0.97])
        v, n = settings.deep_videos, settings.deep_per_source
        factories: dict[str, tuple[Callable[[], Awaitable[list[PhotoCandidate]]], int]] = {
            "tiktok_top": (lambda: social_api.tiktok_top(self.uni, n, v), n),
            "tiktok_hashtag": (lambda: social_api.tiktok_hashtag(self.uni, n, v), n),
            "tiktok_search": (lambda: social_api.tiktok_search(self.uni, n, v), n),
        }
        ig = self.uni.social.get("instagram")
        if ig:
            factories["instagram"] = (lambda: social_api.instagram_posts(ig, n), n)
            factories["instagram_tagged"] = (lambda: social_api.instagram_tagged(ig, n), n)
        tt = self.uni.social.get("tiktok")
        if tt:
            factories["tiktok"] = (lambda: social_api.tiktok_videos(tt, n, v), n)
        tasks = {asyncio.create_task(self.source_pipeline(f"{name}", f(), lim), name=name): name
                 for name, (f, lim) in factories.items()}
        done, pending = await asyncio.wait(tasks.keys(), timeout=max(self.collect_deadline + 2.0 - time.monotonic(), 5.0))
        for t in pending:
            t.cancel()
        if self.inspector:
            await self.inspector.finish(timeout=max(3.0, self.deadline - time.monotonic()))
            self.logf(f"deep pass: inspector judged {len(self.inspector.verdicts)} photos in total")
        await self.stage("deep", "done", count=len(self.fetched), detail=f"кандидатов всего: {len(self.fetched)}")


async def run(qid: str, emit: Emit) -> Profile:
    r = Run(qid, emit)
    try:
        return await r.run()
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline failed for %s", qid)
        await emit({"type": "error", "message": f"{type(e).__name__}: {e}", "log": r.log, "elapsed_ms": r.ms()})
        raise
