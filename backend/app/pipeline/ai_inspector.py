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

from PIL import Image
from pydantic import BaseModel, Field

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
- q 0-3: usefulness for a prospective student: 0 = strip, banner, crop, detail or blur; 3 = clear informative view.
- flags: illustration, stock (staged stock photo), banner (text or graphics overlay), crop, text, portrait (one or two people fill the frame), collage, screenshot, logo, official_meeting (officials at a table or ceremony).
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
        },
        "required": ["n", "place", "rel", "cat", "q", "flags", "era"],
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


# model -> monotonic time until which it is not asked again (a spent daily quota; failed calls are not billed)
_spent: dict[str, float] = {}
SPENT_S = 3600.0


def gemini_models() -> list[str]:
    """Inspector models in order of preference, minus the ones whose daily quota ran out within the last hour."""
    now = time.monotonic()
    models = list(dict.fromkeys([settings.inspect_model, *settings.inspect_models]))
    return [m for m in models if _spent.get(m, 0.0) <= now]


async def gemini_post(body: dict, timeout: float) -> tuple[dict, str]:
    """One generateContent call for the description and the comparison, on the first model with quota left.
    Returns (response json, model). A spent daily quota moves on to the next model instead of failing."""
    for model in gemini_models():
        r = await http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                            json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=timeout)
        if r.status_code == 429 and _daily_quota(r):
            _spent[model] = time.monotonic() + SPENT_S
            continue
        r.raise_for_status()
        return r.json(), model
    raise QuotaExhausted("daily quota spent on every model")


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
        while self.queue and len(self.tasks) < settings.inspect_concurrency:
            t = asyncio.create_task(self._worker())
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)

    async def _worker(self) -> None:
        size = settings.inspect_batch
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
            # the fast profile's deadline: what this worker held goes back in line, and the deep pass picks it up
            for f in batch:
                if f.id not in self.verdicts:
                    self.seq += 1
                    heapq.heappush(self.queue, (0.0, self.seq, f))
            raise

    async def finish(self, timeout: float) -> None:
        """Waits for the queue to drain; at the deadline the workers stop and the rest stays queued (see _worker)."""
        end = time.monotonic() + timeout
        while self.tasks and time.monotonic() < end:
            await asyncio.wait(list(self.tasks), timeout=max(0.0, end - time.monotonic()))
            self._kick()   # a worker that returned while photos were still arriving
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
                          model=self.last_model or self.model)
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
        models = gemini_models()
        if not models:
            self.quota_out = True
            raise QuotaExhausted("daily quota spent on every inspector model")
        for model in models:
            try:
                return await self._gemini_one(items, model)
            except QuotaExhausted:
                _spent[model] = time.monotonic() + SPENT_S
                log.warning("inspector: daily quota of %s is spent, moving to the next model", model)
        self.quota_out = True
        raise QuotaExhausted("daily quota spent on every inspector model")

    async def _gemini_one(self, items: list[Fetched], model: str) -> list[_Item]:
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = None
        for attempt in range(3):
            r = await http.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key},
                                timeout=settings.inspect_timeout_s)
            if r.status_code == 429 and _daily_quota(r):
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
        self.last_model = model
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
