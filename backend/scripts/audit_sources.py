"""Live audit of the photo sources: every source is called NOW with the response caches bypassed.

For each university and source it records the HTTP calls (host, status, latency), the candidates, how many images
actually download and decode, video downloads for frame extraction, and the AI inspector's verdict on each image.
Per-query stats for the social searches (which query brought which post) go to the same report, thumbnails to
<out>/thumbs/<qid>/<source>/ for contact sheets.

    python scripts/audit_sources.py --out <dir> [--qids Q2783344,Q7842] [--sources tiktok_top,instagram_search]
                                    [--no-inspect]
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache, http  # noqa: E402
from app.config import settings  # noqa: E402
from app.geo import CampusGeom  # noqa: E402
from app.models import SOURCE_LABELS, University  # noqa: E402
from app.pipeline import video_frames  # noqa: E402
from app.pipeline.ai_inspector import Inspector  # noqa: E402
from app.pipeline.fetch import Fetched, fetch_all  # noqa: E402
from app.pipeline.sources import (map_reviews, mapillary, official_site, social, social_api, vk_geo,  # noqa: E402
                                  web_images)
from app.pipeline.verify import hard_flags, useful_photo  # noqa: E402

DEFAULT_QIDS = ["Q2783344", "Q1734762", "Q4227527", "Q7842", "Q1204714"]
SRC: contextvars.ContextVar[str] = contextvars.ContextVar("src", default="?")
calls: list[dict] = []          # every HTTP call: src, host, path, status, ms, bytes
sc_calls: list[dict] = []       # every ScrapeCreators call with the ids it returned
serper_calls: list[dict] = []
video_dl: list[dict] = []

# ---------- instrumentation ----------
_get, _post = http.get, http.post


async def _logged(fn, url, kw, method):
    t = time.monotonic()
    rec = {"src": SRC.get(), "method": method, "host": urlparse(url).netloc, "path": urlparse(url).path[:80]}
    try:
        r = await fn(url, **kw)
        rec.update(status=r.status_code, bytes=len(r.content or b""))
        return r
    except Exception as e:  # noqa: BLE001
        rec.update(status=f"EXC {type(e).__name__}", bytes=0)
        raise
    finally:
        rec["ms"] = int((time.monotonic() - t) * 1000)
        calls.append(rec)


async def get(url, **kw):
    return await _logged(_get, url, kw, "GET")


async def post(url, **kw):
    return await _logged(_post, url, kw, "POST")


http.get, http.post = get, post

# response caches off: every call below reaches the real API
_kv_get = cache.kv_get


async def kv_get(bucket, key, max_age_s=None):
    if bucket in ("social", "place"):
        return None
    return await _kv_get(bucket, key, max_age_s)


cache.kv_get = kv_get
video_frames.ready = lambda key: []     # re-download every clip: we are testing the downloads too

_sc_fetch = social_api._sc_fetch


def _ids(j: dict | None) -> list[str]:
    out = []
    for k in ("items", "posts", "aweme_list", "search_item_list", "videos"):
        for it in (j or {}).get(k) or []:
            a = it.get("aweme_info", it) if isinstance(it, dict) else {}
            v = a.get("shortcode") or a.get("code") or a.get("aweme_id") or a.get("id")
            if v:
                out.append(str(v))
    return out


async def sc_fetch(path, key, params):
    t = time.monotonic()
    j = await _sc_fetch(path, key, params)
    sc_calls.append({"src": SRC.get(), "path": path, "params": {k: v for k, v in params.items() if k != "cursor"},
                     "page2": "cursor" in params, "ok": j is not None, "ids": _ids(j),
                     "ms": int((time.monotonic() - t) * 1000),
                     "credits_left": (j or {}).get("credits_remaining")})
    return j


social_api._sc_fetch = sc_fetch

_serper = web_images._serper


async def serper(q, gl, hl, num=10):
    res = await _serper(q, gl, hl, num)
    serper_calls.append({"src": SRC.get(), "q": q, "gl": gl, "hl": hl, "n": len(res or []),
                         "urls": [r.get("imageUrl") for r in (res or [])]})
    return res


web_images._serper = serper

_download = video_frames._download


async def download(url, duration_s, dests, key, timeout):
    t = time.monotonic()
    got = await _download(url, duration_s, dests, key, timeout)
    video_dl.append({"src": SRC.get(), "host": urlparse(url).netloc, "ok": bool(got), "frames": len(got),
                     "ms": int((time.monotonic() - t) * 1000)})
    return got


video_frames._download = download


# ---------- one university ----------
def factories(uni: University, geom: CampusGeom) -> dict:
    s = uni.social
    f = {
        "official": lambda: official_site.collect(uni),
        "telegram": (lambda: social.telegram(s["telegram"], 30)) if s.get("telegram") else None,
        "youtube": (lambda: social.youtube(s["youtube"], 24)) if s.get("youtube") else None,
        "vk": (lambda: social.vk(s["vk"], 40)) if s.get("vk") else None,
        "instagram": (lambda: social_api.instagram_posts(s["instagram"], 40)) if s.get("instagram") else None,
        "instagram_tagged": (lambda: social_api.instagram_tagged(s["instagram"], 40)) if s.get("instagram") else None,
        "tiktok": (lambda: social_api.tiktok_videos(s["tiktok"], 40, 10)) if s.get("tiktok") else None,
        "tiktok_search": lambda: social_api.tiktok_search(uni, 40, 10),
        "tiktok_hashtag": lambda: social_api.tiktok_hashtag(uni, 40, 10),
        "tiktok_top": lambda: social_api.tiktok_top(uni, 60, 10, pages=3, deep=True),
        "instagram_search": lambda: social_api.instagram_search(uni, 60, 10, pages=2, deep=True),
        "youtube_search": lambda: social_api.youtube_search(uni),
        "web_image": lambda: web_images.google_images(uni, deep=True),
        "web_image_fast": lambda: web_images.google_images(uni, deep=False),
        "map_review": lambda: map_reviews.collect(uni),
        "vk_geo": lambda: vk_geo.collect(uni),
        "mapillary": lambda: mapillary.collect(geom.bbox(pad_m=120), 20),
        "commons_search": lambda: web_images.commons_search(uni),
        "openverse": lambda: web_images.openverse(uni),
    }
    return f


def describe(geom: CampusGeom):
    def fn(f: Fetched) -> str:
        c = f.cand
        cap = " ".join((c.title or c.text or "").split())[:90]
        u = urlparse(c.page_url)
        page = (u.netloc.replace("www.", "") + u.path)[:70]
        lat, lon = (c.lat, c.lon) if c.lat is not None else (f.exif_lat, f.exif_lon)
        if lat is None or lon is None:
            geo = "none"
        else:
            d = geom.distance_m(lat, lon)
            geo = "inside the campus outline" if d == 0 else f"{d:.0f} m from the campus"
        found = f"; found by image search {c.collector.split(':', 1)[1]!r}" if (c.collector or "").startswith("google:") else ""
        return (f"source={SOURCE_LABELS.get(c.source, c.source)}{found}; caption={cap!r}; page={page}; geotag={geo}; "
                f"size={f.width}x{f.height}; date={c.date or f.exif_date or '?'}")
    return fn


async def run_one(qid: str, only: set[str] | None, out: Path, inspect: bool) -> dict:
    row = await asyncio.to_thread(_profile_row, qid)
    p = json.loads(row)
    uni = University.model_validate(p["university"])
    camp = p.get("campus") or {}
    geom = CampusGeom(camp.get("polygon"), (uni.lat, uni.lon), camp.get("radius_m") or 500.0)
    res: dict = {"qid": qid, "name": uni.names.get("en") or uni.name, "social_links": uni.social,
                 "queries_fast": social_api.social_queries(uni, False), "queries_deep": social_api.social_queries(uni, True),
                 "hashtags": social_api.hashtags(uni), "sources": {}}
    print(f"\n=== {qid} {res['name']}  links={list(uni.social)}", flush=True)

    async def one(name: str, fac) -> None:
        SRC.set(name)
        t = time.monotonic()
        if fac is None:
            res["sources"][name] = {"status": "no_link"}
            return
        try:
            cands = await asyncio.wait_for(fac(), timeout=300)
            err = None
        except Exception as e:  # noqa: BLE001
            cands, err = [], f"{type(e).__name__}: {e}"[:200]
        t_collect = int((time.monotonic() - t) * 1000)
        SRC.set(name + ":img")
        fetched = await fetch_all(cands, deadline=math.inf)
        res["sources"][name] = {"status": "error" if err else "ok", "error": err, "ms": t_collect,
                                "cands": len(cands), "uniq": len({c.url for c in cands}), "fetched": len(fetched),
                                "_fetched": fetched}
        print(f"  {name:17} {('ERR ' + err) if err else 'ok':40.40} cands={len(cands):3} fetched={len(fetched):3} "
              f"{t_collect/1000:5.1f}s", flush=True)

    facs = factories(uni, geom)
    await asyncio.gather(*[one(n, f) for n, f in facs.items() if not only or n in only])

    # the inspector sees every downloaded image, with the university's reference photo, as in a real build
    verdicts = {}
    if inspect and settings.active_llm() != "none":
        SRC.set("inspector")
        insp = Inspector(uni, describe(geom))
        ref = None
        if uni.image_url:
            try:
                from io import BytesIO
                from PIL import Image
                r = await http.get(uni.image_url, timeout=6.0)
                if r.status_code == 200:
                    ref = Image.open(BytesIO(r.content)).convert("RGB")
            except Exception:  # noqa: BLE001
                ref = None
        insp.set_reference(ref)
        insp.cap = 10_000
        allf = [f for s in res["sources"].values() for f in s.get("_fetched", [])]
        insp.submit(allf)
        t = time.monotonic()
        await insp.finish(timeout=900)
        verdicts = insp.verdicts
        res["inspector"] = {"submitted": len(allf), "judged": len(verdicts), "cached": insp.cached, "calls": insp.calls,
                            "errors": insp.errors, "quota_out": insp.quota_out, "models": insp.used,
                            "ms": int((time.monotonic() - t) * 1000)}
        print(f"  inspector: {res['inspector']}", flush=True)

    # thumbnails + per-image records
    for name, s in res["sources"].items():
        fetched = s.pop("_fetched", [])
        d = out / "thumbs" / qid / name
        d.mkdir(parents=True, exist_ok=True)
        recs = []
        for i, f in enumerate(fetched):
            im = f.image.copy()
            im.thumbnail((360, 360))
            im.save(d / f"{i:03d}.jpg", quality=82)
            v = verdicts.get(f.id)
            recs.append({"i": i, "id": f.id, "url": f.cand.url[:300], "page": f.cand.page_url, "title": f.cand.title,
                         "collector": f.cand.collector, "w": f.width, "h": f.height, "phash": f.phash,
                         "ai": v.model_dump() if v else None,
                         "useful": bool(v and useful_photo(v) and not hard_flags(v))})
        s["images"] = recs
        s["ai_judged"] = sum(r["ai"] is not None for r in recs)
        s["ai_useful"] = sum(r["useful"] for r in recs)
    return res


def _profile_row(qid: str) -> str:
    import sqlite3
    c = sqlite3.connect(settings.data_dir / "campuslens.sqlite3")
    try:
        return c.execute("select json from profiles where qid=?", (qid,)).fetchone()[0]
    finally:
        c.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--qids", default=",".join(DEFAULT_QIDS))
    ap.add_argument("--sources", default="")
    ap.add_argument("--no-inspect", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    await cache.init()
    only = set(filter(None, a.sources.split(","))) or None
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "unis": []}
    for qid in a.qids.split(","):
        report["unis"].append(await run_one(qid, only, out, not a.no_inspect))
        report.update(calls=calls, sc_calls=sc_calls, serper_calls=serper_calls, video_dl=video_dl)
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")
    await http.close()
    by = defaultdict(Counter)
    for c in calls:
        by[c["src"].split(":")[0]][str(c.get("status", "cancelled"))] += 1
    print("\nHTTP status by source:")
    for s, cnt in sorted(by.items()):
        print(f"  {s:18} {dict(cnt)}")


if __name__ == "__main__":
    asyncio.run(main())
