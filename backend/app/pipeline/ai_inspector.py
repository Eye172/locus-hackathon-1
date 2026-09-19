"""AI inspector: a vision model looks at EVERY candidate photo next to a trusted reference photo of the university.

One request carries up to `inspect_batch` photos at low media resolution (280 tokens each on Gemini 3) plus their
metadata. For each photo the model answers: where it is (this university / its city / another place / not a photo),
how sure it is, what it shows, how useful it is for a prospective student, quality flags (stock, banner, crop,
illustration…) and the probable decade. The verdict feeds the confidence score (verify.py) and the curator.

Batches are sent while sources are still streaming in, so most verdicts are ready when collection ends.
Verdicts are cached per (university, image SHA-1): a rebuilt profile costs nothing.
"""
from __future__ import annotations

import asyncio
import base64
import heapq
import io
import logging
import time
from typing import Callable, Literal

import httpx
from PIL import Image
from pydantic import BaseModel, Field

from . import vertex
from .. import cache, http
from ..config import settings
from ..models import AiVerdict, University
from .fetch import Fetched

log = logging.getLogger("campuslens.inspector")

SIDE = 384  # px, longest side sent to the model
PLACES = ["this_university", "city", "other_place", "unknown", "not_photo"]
CATS = ["campus", "dormitory", "classroom", "library", "lab", "sports", "student_life", "city", "none"]
FLAGS = ["illustration", "stock", "banner", "crop", "text", "portrait", "collage", "screenshot", "logo", "official_meeting"]
ERAS = ["2020s", "2010s", "2000s", "older", "unknown"]

FLAG_RU = {
    "illustration": "рисунок или рендер", "stock": "стоковое фото", "banner": "баннер с графикой", "crop": "обрезанный фрагмент",
    "text": "много текста", "portrait": "крупный план людей", "collage": "коллаж", "screenshot": "скриншот",
    "logo": "логотип", "official_meeting": "официальная встреча",
}
PLACE_RU = {"this_university": "этот вуз", "city": "город", "other_place": "другое место", "unknown": "не определить",
            "not_photo": "не фотография"}
ERA_RU = {"2020s": "2020-е", "2010s": "2010-е", "2000s": "2000-е", "older": "до 2000"}

PROMPT = """You inspect candidate photos for a verified visual profile of the university "{name}" in {city}.
{refline}Candidates 1..{k} with metadata (source, caption, page, geotag, size):
{meta}
Return one item per candidate, n = its number:
- place: this_university = its campus, buildings, rooms, labs, dorms, sports facilities or its student events; city = the city itself (streets, landmarks, theatres, churches, mosques, parks, views), not the university; other_place = a different identifiable institution or building; unknown; not_photo = drawing, render, logo, screenshot, document.
- rel 0-3: how sure you are that the photo shows THIS university (3 = matches the reference, or its name/logo is visible, or the metadata clearly ties it and the picture fits; 0 = no). For place=city give rel for "shows this city".
- cat: campus|dormitory|classroom|library|lab|sports|student_life|city|none.
- q 0-3: how much the photo shows what it is like to be at this university - the place itself: buildings, halls, rooms, labs, the library, a dorm room, a canteen, an event with its venue. 3 = the place is clearly visible and informative (people may be in it); 2 = the place is visible but partly covered or ordinary; 1 = mostly faces, a selfie, a close-up of food/objects/screens, a dark or blurred frame, a frame where big text covers the picture; 0 = poster, title card, document, graphic, strip, crop.
- flags: illustration, stock (staged stock photo), banner (text or graphics overlay), crop, text, portrait (one or two people fill the frame and hide the place), collage, screenshot, logo, official_meeting (officials at a table or ceremony, a press photo of a signing, a podium speech).
Frames from students' videos and photos from social networks are welcome when they show the place: judge the picture, not the caption.
- beauty 0-3: how good it is as a picture, whatever it shows: 3 = striking (good light, sharp, well composed, the place looks inviting); 2 = clean and good; 1 = ordinary: flat light, clutter, slightly soft; 0 = dark, blurred, tilted, overexposed, or covered by text or stickers.
- era: probable decade the photo was taken.
- why: only if rel<=1 or any flag: at most 6 Russian words; otherwise "".
Judge buildings strictly: a building unlike the reference and not tied to the university by its metadata is not the campus."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "n": {"type": "INTEGER"},
            "place": {"type": "STRING", "enum": PLACES},
            "rel": {"type": "INTEGER"},
            "cat": {"type": "STRING", "enum": CATS},
            "q": {"type": "INTEGER"},
            "flags": {"type": "ARRAY", "items": {"type": "STRING", "enum": FLAGS}},
            "era": {"type": "STRING", "enum": ERAS},
            "why": {"type": "STRING"},
            "beauty": {"type": "INTEGER"},
        },
        "required": ["n", "place", "rel", "cat", "q", "flags", "era", "beauty"],
    }}},
    "required": ["items"],
}


class _Item(BaseModel):
    n: int
    place: Literal["this_university", "city", "other_place", "unknown", "not_photo"]
    rel: int = Field(ge=0, le=3)
    cat: Literal["campus", "dormitory", "classroom", "library", "lab", "sports", "student_life", "city", "none"]
    q: int = Field(ge=0, le=3)
    flags: list[Literal["illustration", "stock", "banner", "crop", "text", "portrait", "collage", "screenshot", "logo",
                        "official_meeting"]] = Field(default_factory=list)
    era: Literal["2020s", "2010s", "2000s", "older", "unknown"] = "unknown"
    why: str = ""
    beauty: int = Field(default=1, ge=0, le=3)


class _Batch(BaseModel):
    items: list[_Item]


def jpeg_b64(im: Image.Image, side: int = SIDE) -> str:
    im = im.copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _clip(v: int) -> int:
    return max(0, min(3, int(v)))


class QuotaExhausted(RuntimeError):
    """Every model's daily quota is spent: retrying in smaller halves would only spend more time."""


class _KeyBlocked(RuntimeError):
    """This key's project does not allow the Gemini API: the next key is tried."""


# (key number, model) -> monotonic time until which it is not asked again (a spent daily quota; failed calls are not
# billed); key number -> the same for a key its project does not allow Gemini for (API disabled or key restricted)
_spent: dict[tuple[int, str], float] = {}
_blocked: dict[int, float] = {}
SPENT_S = 3600.0


def gemini_routes() -> list[tuple[int, str, str]]:
    """(key number, key, model) in order of preference. Route 0 is Vertex AI (paid from the Cloud credits, no daily
    cap) when its service account is there; then the AI Studio keys - the best model on every key before a weaker
    model, since each Google Cloud project has its own daily quota - minus what is spent or blocked for the hour."""
    now = time.monotonic()
    routes: list[tuple[int, str, str]] = []
    if vertex.available() and _blocked.get(0, 0.0) <= now:
        routes += [(0, "", m) for m in dict.fromkeys(settings.vertex_models) if _spent.get((0, m), 0.0) <= now]
    models = list(dict.fromkeys([settings.inspect_model, *settings.inspect_models]))
    keys = settings.gemini_keys()
    routes += [(k, key, m) for m in models for k, key in enumerate(keys, 1)
               if _spent.get((k, m), 0.0) <= now and _blocked.get(k, 0.0) <= now]
    return routes


async def _endpoint(k: int, key: str, model: str) -> tuple[str, dict]:
    if k == 0:
        return vertex.url(model), await vertex.headers()
    return (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": key})


def parallel() -> int:
    """Batches in flight at once: the free tier answers 429 above ~15 requests a minute, Vertex takes many more."""
    return settings.vertex_concurrency if vertex.available() and _blocked.get(0, 0.0) <= time.monotonic() \
        else settings.inspect_concurrency


def gemini_models() -> list[str]:
    """Models that still have a key with quota left, best first."""
    return list(dict.fromkeys(m for _, _, m in gemini_routes()))


def _unusable(k: int, r, model: str = "") -> bool:
    """A 403 of a key whose project has the Gemini API switched off or whose restrictions leave it out; a 404 of a
    model this route does not have (Vertex has not every AI Studio model)."""
    if r.status_code == 404 and model:
        _spent[(k, model)] = time.monotonic() + SPENT_S
        log.warning("gemini route #%d has no model %s", k, model)
        return True
    if r.status_code in (401, 403):
        _blocked[k] = time.monotonic() + SPENT_S
        log.warning("gemini key #%d is not allowed to call the Gemini API (%s)", k, r.text[:120].replace("\n", " "))
        return True
    return False


async def gemini_post(body: dict, timeout: float) -> tuple[dict, str]:
    """One generateContent call for the description, the comparison and the campus facts, on the first key and
    model with quota left. Returns (response json, model)."""
    for k, key, model in gemini_routes():
        try:
            url, hdrs = await _endpoint(k, key, model)
        except Exception as e:  # noqa: BLE001  (a service-account token that cannot be issued)
            log.warning("vertex token failed: %r", e)
            _blocked[k] = time.monotonic() + SPENT_S
            continue
        r = await http.post(url, json=body, headers=hdrs, timeout=timeout)
        if r.status_code == 429 and (k == 0 or not _daily_quota(r)):
            await asyncio.sleep(2.0)      # busy for a moment (Vertex has no daily cap): once more, then the next route
            r = await http.post(url, json=body, headers=hdrs, timeout=timeout)
        if r.status_code == 429 and k and _daily_quota(r):
            _spent[(k, model)] = time.monotonic() + SPENT_S
            continue
        if _unusable(k, r, model) or r.status_code == 429:
            continue
        r.raise_for_status()
        return r.json(), model
    raise QuotaExhausted("daily quota spent on every key and model")


def vertex_variants(model: str, body_for: Callable[[str], dict]) -> list[tuple[str, str, dict]]:
    """(model, url, body) for the first request and each copy: the model asked for, then settings.vertex_fallbacks."""
    out = [(model, vertex.url(model), body_for(model))]
    out += [(m, vertex.url(m, loc), body_for(m)) for m, loc in settings.vertex_fallbacks if (m, loc) != (model, "global")]
    return out


def thinking(model: str, level: str = "minimal") -> dict:
    """The least thinking a model allows: Gemini 3 takes a level, 2.5 a token budget (0 = off on flash-lite)."""
    if model.startswith("gemini-3"):
        return {"thinkingLevel": level}
    return {"thinkingBudget": 0 if level == "minimal" else 1024}


async def _vertex_hedged(url: str | list[tuple[str, str, dict]], body: dict | None, hdrs: dict, timeout: float,
                         hedge_s: float | None = None) -> httpx.Response:
    """One request to Vertex, sent again when it hangs (see settings.vertex_hedge_s): a copy goes out after
    vertex_hedge_s without an answer, or at once after a 429/5xx, up to vertex_copies; the first 200 wins and the
    others are cancelled. `url` may be a list of (model, url, body) variants (vertex_variants): the copies then go to
    the fallback models in turn, and the answer's `model_used` attribute says which one answered. Returns the last
    answer, or raises a timeout when none came within `timeout`."""
    variants = url if isinstance(url, list) else [("", url, body)]
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    running: set[asyncio.Task] = set()
    sent = 0
    last: httpx.Response | BaseException | None = None

    async def one(m: str, u: str, b: dict) -> httpx.Response:
        r = await http.post(u, json=b, headers=hdrs, timeout=max(1.0, end - loop.time()))
        r.model_used = m   # type: ignore[attr-defined]
        return r

    def send() -> None:
        nonlocal sent
        m, u, b = variants[min(sent, len(variants) - 1)]
        sent += 1
        t = asyncio.create_task(one(m, u, b))
        t.copy_no = sent - 1   # type: ignore[attr-defined]
        # a copy that loses the race may still fail on its own afterwards: read its outcome so it is not reported
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        running.add(t)

    hedge = hedge_s or settings.vertex_hedge_s
    send()
    next_copy = loop.time() + hedge
    try:
        while running and loop.time() < end:
            until = min(end, next_copy) if sent < settings.vertex_copies else end
            done, _ = await asyncio.wait(running, timeout=max(0.0, until - loop.time()), return_when=asyncio.FIRST_COMPLETED)
            failed = False
            for t in done:
                running.discard(t)
                try:
                    r = t.result()
                except Exception as e:  # noqa: BLE001  (a timeout or a dropped connection of this copy)
                    last, failed = e, True
                    continue
                if r.status_code == 200:
                    return r
                if r.status_code not in (429, 500, 503) and getattr(t, "copy_no", 0) == 0:
                    return r          # the model asked for says no (bad request, no access, no model): final
                if last is None or not isinstance(last, httpx.Response) or getattr(t, "copy_no", 0) == 0:
                    last = r          # a fallback's refusal is only kept when nothing better is known
                failed = True
            if sent < settings.vertex_copies and (failed or loop.time() >= next_copy):
                send()
                next_copy = loop.time() + hedge
    finally:
        for t in running:
            t.cancel()
    if isinstance(last, httpx.Response):
        return last
    raise httpx.ReadTimeout(f"vertex: no answer from {sent} copies in {timeout:.0f} s")


def _daily_quota(r) -> bool:
    try:
        return "PerDay" in r.text
    except Exception:  # noqa: BLE001
        return False


class Inspector:
    """One per profile build. `submit()` is non-blocking; `finish()` waits for the outstanding batches."""

    def __init__(self, uni: University, describe_fn: Callable[[Fetched], str]) -> None:
        self.uni = uni
        self.describe = describe_fn
        self.provider = settings.active_llm()
        self.model = settings.inspect_model if self.provider == "gemini" else settings.claude_model
        self.verdicts: dict[str, AiVerdict] = {}
        self.submitted: set[str] = set()
        self.tasks: set[asyncio.Task] = set()      # the workers
        self.queue: list[tuple[float, int, Fetched]] = []   # heap of (-priority, order, photo)
        self.seq = 0
        self.cap = settings.inspect_max_photos
        self.ref_b64: str | None = None
        self.ref_ready = asyncio.Event()
        self.calls = self.tokens_in = self.tokens_out = self.errors = self.cached = 0
        self.used: dict[str, int] = {}          # model -> answered requests in this build
        self.last_model: str | None = None
        self.quota_out = False                  # every model's daily quota was spent during this build
        self.ms = 0

    # ---------- reference ----------
    def set_reference(self, image: Image.Image | None) -> None:
        if image is not None:
            self.ref_b64 = jpeg_b64(image)
        self.ref_ready.set()

    # ---------- queue ----------
    # The inspector's attention is the scarce resource: a few requests a second, a daily quota, a time budget. With
    # the broad search it gets several hundred candidates, most of them posters and screenshots. So it is a priority
    # queue, not a first-come line: the most promising photos are looked at first, and whatever is still waiting when
    # time runs out is the least promising - rejected unseen if it came from a search, which is the safe side.
    def submit(self, items: list[Fetched], priority: Callable[[Fetched], float] | None = None) -> None:
        for f in items:
            if f.id in self.submitted or len(self.submitted) >= self.cap:
                continue
            self.submitted.add(f.id)
            self.seq += 1
            heapq.heappush(self.queue, (-(priority(f) if priority else 0.0), self.seq, f))
        self._kick()

    def _kick(self) -> None:
        while self.queue and len(self.tasks) < parallel():
            t = asyncio.create_task(self._worker())
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)

    async def _worker(self) -> None:
        # Vertex has no daily request cap: smaller batches answer sooner, so photos arriving late still make the first
        # profile; the free tier counts requests, so there the batch stays large
        size = 8 if parallel() > settings.inspect_concurrency else settings.inspect_batch
        batch: list[Fetched] = []
        try:
            while self.queue:
                batch = []
                while self.queue and len(batch) < size:
                    batch.append(heapq.heappop(self.queue)[2])
                todo: list[Fetched] = []
                for f in batch:
                    hit = await cache.kv_get("inspect", self._key(f))
                    if hit:
                        self.verdicts[f.id] = AiVerdict.model_validate(hit)
                        self.cached += 1
                    else:
                        todo.append(f)
                if todo:
                    await self._batch(todo)
                batch = []
                if self.quota_out:
                    return
        except asyncio.CancelledError:
            # stopped at a deadline: what this worker held goes back in line for whoever drains the queue next
            for f in batch:
                if f.id not in self.verdicts:
                    self.seq += 1
                    heapq.heappush(self.queue, (0.0, self.seq, f))
            raise

    async def finish(self, timeout: float, stop: bool = True) -> None:
        """Waits for the queue to drain; at the deadline the workers stop and the rest stays queued (see _worker).
        With stop=False they keep going after it - the first profile takes the verdicts so far, the background pass
        the rest."""
        end = time.monotonic() + timeout
        while self.tasks and time.monotonic() < end:
            await asyncio.wait(list(self.tasks), timeout=max(0.0, end - time.monotonic()))
            self._kick()   # a worker that returned while photos were still arriving
        if not stop:
            return
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def _key(self, f: Fetched) -> str:
        return f"{self.uni.qid}:{f.sha1 or f.id}"

    async def _batch(self, items: list[Fetched], retry: bool = True) -> None:
        if not items:
            return
        try:
            await asyncio.wait_for(self.ref_ready.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        t = time.monotonic()   # concurrency is the number of workers (see _kick)
        try:
            got = await (self._gemini(items) if self.provider == "gemini" else self._claude(items))
        except QuotaExhausted:
            self.errors += 1
            return  # no model can answer today: splitting the batch would only fail twice more
        except Exception as e:  # noqa: BLE001
            self.errors += 1
            log.warning("inspector batch failed (%d photos): %r", len(items), e)
            got = None
        finally:
            self.ms += int((time.monotonic() - t) * 1000)
        if got is None:
            if retry:  # transient 503/timeouts and the odd malformed answer: try again in two smaller halves
                half = max(1, len(items) // 2)
                await asyncio.gather(self._batch(items[:half], False), self._batch(items[half:], False))
            return
        for it in got:
            if not 1 <= it.n <= len(items):
                continue
            f = items[it.n - 1]
            v = AiVerdict(place=it.place, rel=_clip(it.rel), cat=it.cat, q=_clip(it.q),
                          flags=list(dict.fromkeys(it.flags)), era=it.era, why=(it.why or "").strip()[:80],
                          model=self.last_model or self.model, beauty=_clip(it.beauty))
            self.verdicts[f.id] = v
            await cache.kv_set("inspect", self._key(f), v.model_dump())

    def stats(self) -> dict:
        return {"provider": self.provider, "model": self.model, "photos": len(self.verdicts), "submitted": len(self.submitted),
                "cached": self.cached, "calls": self.calls, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "ms": self.ms, "errors": self.errors, "reference": self.ref_b64 is not None,
                "models": self.used, "quota_out": self.quota_out}

    # ---------- prompt ----------
    def _prompt(self, items: list[Fetched]) -> str:
        u = self.uni
        names = " / ".join(dict.fromkeys(x for x in [u.name, u.names.get("en"), u.names.get("kk")] if x))
        refline = ("Image R is a trusted reference photo of this university's main building.\n" if self.ref_b64
                   else "No reference photo is available; rely on the metadata and what the image shows.\n")
        meta = "\n".join(f"{n}. {self.describe(f)}" for n, f in enumerate(items, 1))
        return PROMPT.format(name=names, city=u.city or "its city", refline=refline, k=len(items), meta=meta)

    # ---------- providers ----------
    async def _gemini(self, items: list[Fetched]) -> list[_Item]:
        routes = gemini_routes()
        if not routes:
            self.quota_out = True
            raise QuotaExhausted("daily quota spent on every inspector model")
        for k, key, model in routes:
            if _spent.get((k, model), 0.0) > time.monotonic() or _blocked.get(k, 0.0) > time.monotonic():
                continue      # another batch found it spent a moment ago
            if k == 0 and model not in settings.vertex_inspect_models:
                continue
            try:
                return await self._gemini_one(items, model, k, key)
            except httpx.HTTPStatusError as e:
                if k == 0 and (e.response.status_code >= 500 or e.response.status_code == 429):
                    continue      # Vertex short of capacity even after the copies: the free key takes this batch
                raise
            except httpx.TransportError:
                if k == 0:
                    continue      # no answer from Vertex in time (timeouts, dropped connections): the same
                raise
            except QuotaExhausted:
                _spent[(k, model)] = time.monotonic() + SPENT_S
                log.warning("inspector: daily quota of %s on key #%d is spent, moving on", model, k)
            except _KeyBlocked:
                continue
        self.quota_out = True
        raise QuotaExhausted("daily quota spent on every inspector model")

    async def _gemini_one(self, items: list[Fetched], model: str, k: int = 1, key: str | None = None) -> list[_Item]:
        parts: list[dict] = [{"text": self._prompt(items)}]
        if self.ref_b64:
            parts += [{"text": "R:"}, {"inline_data": {"mime_type": "image/jpeg", "data": self.ref_b64}}]
        for n, f in enumerate(items, 1):
            parts += [{"text": f"{n}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": jpeg_b64(f.image)}}]
        gen: dict = {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.1,
                     "mediaResolution": "MEDIA_RESOLUTION_LOW"}
        if model.startswith("gemini-3"):
            gen["thinkingConfig"] = {"thinkingLevel": "minimal"}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        url, hdrs = await _endpoint(k, key or settings.gemini_api_key or "", model)
        r = None
        for attempt in range(3):
            if k == 0:
                # Vertex: the copies are the retries (_vertex_hedged), the later ones to the fallback models; what
                # fails after them goes to the next route
                variants = vertex_variants(model, lambda m: {**body, "generationConfig": {**gen, "thinkingConfig": thinking(m)}})
                r = await _vertex_hedged(variants, None, hdrs, settings.inspect_timeout_s)
                if _unusable(k, r, model):
                    raise _KeyBlocked(k)
                model = getattr(r, "model_used", "") or model
                break
            r = await http.post(url, json=body, headers=hdrs, timeout=settings.inspect_timeout_s)
            if _unusable(k, r, model):
                raise _KeyBlocked(k)
            if r.status_code == 429 and k and _daily_quota(r):
                # the day's requests are gone: waiting a few seconds changes nothing, the next model might answer
                raise QuotaExhausted(model)
            if r.status_code in (429, 500, 503) and attempt < 2:
                # rate limit or overload: wait as asked (free tier) or back off, then try again
                try:
                    wait = float(r.headers.get("retry-after", ""))
                except ValueError:
                    wait = 2.0 * (attempt + 1)
                await asyncio.sleep(min(wait, 6.0))
                continue
            break
        r.raise_for_status()
        j = r.json()
        self.calls += 1
        usage = j.get("usageMetadata") or {}
        self.tokens_in += usage.get("promptTokenCount", 0)
        self.tokens_out += usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        self.used[model] = self.used.get(model, 0) + 1
        self.last_model = model   # the verdicts of this batch are written under the model that really answered
        return _Batch.model_validate_json(j["candidates"][0]["content"]["parts"][0]["text"]).items

    async def _claude(self, items: list[Fetched]) -> list[_Item]:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key).with_options(
            timeout=settings.inspect_timeout_s, max_retries=0)
        content: list[dict] = []
        if self.ref_b64:
            content += [{"type": "text", "text": "R:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": self.ref_b64}}]
        for n, f in enumerate(items, 1):
            content += [{"type": "text", "text": f"{n}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64(f.image)}}]
        content.append({"type": "text", "text": self._prompt(items)})
        resp = await client.messages.parse(model=self.model, max_tokens=4000, output_format=_Batch,
                                           messages=[{"role": "user", "content": content}])
        self.calls += 1
        self.tokens_in += resp.usage.input_tokens
        self.tokens_out += resp.usage.output_tokens
        return resp.parsed_output.items if resp.parsed_output else []
