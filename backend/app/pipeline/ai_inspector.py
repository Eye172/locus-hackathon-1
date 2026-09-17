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


class Inspector:
    """One per profile build. `submit()` is non-blocking; `finish()` waits for the outstanding batches."""

    def __init__(self, uni: University, describe_fn: Callable[[Fetched], str]) -> None:
        self.uni = uni
        self.describe = describe_fn
        self.provider = settings.active_llm()
        self.model = settings.inspect_model if self.provider == "gemini" else settings.claude_model
        self.verdicts: dict[str, AiVerdict] = {}
        self.submitted: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.sem = asyncio.Semaphore(settings.inspect_concurrency)
        self.cap = settings.inspect_max_photos
        self.ref_b64: str | None = None
        self.ref_ready = asyncio.Event()
        self.calls = self.tokens_in = self.tokens_out = self.errors = self.cached = 0
        self.ms = 0

    # ---------- reference ----------
    def set_reference(self, image: Image.Image | None) -> None:
        if image is not None:
            self.ref_b64 = jpeg_b64(image)
        self.ref_ready.set()

    # ---------- queue ----------
    def submit(self, items: list[Fetched]) -> None:
        new = [f for f in items if f.id not in self.submitted]
        room = self.cap - len(self.submitted)
        new = new[:max(0, room)]
        if not new:
            return
        self.submitted.update(f.id for f in new)
        t = asyncio.create_task(self._handle(new))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _handle(self, items: list[Fetched]) -> None:
        todo: list[Fetched] = []
        for f in items:
            hit = await cache.kv_get("inspect", self._key(f))
            if hit:
                self.verdicts[f.id] = AiVerdict.model_validate(hit)
                self.cached += 1
            else:
                todo.append(f)
        size = settings.inspect_batch
        await asyncio.gather(*[self._batch(todo[i:i + size]) for i in range(0, len(todo), size)])

    def _key(self, f: Fetched) -> str:
        return f"{self.uni.qid}:{f.sha1 or f.id}"

    async def _batch(self, items: list[Fetched], retry: bool = True) -> None:
        if not items:
            return
        try:
            await asyncio.wait_for(self.ref_ready.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        async with self.sem:
            t = time.monotonic()
            try:
                got = await (self._gemini(items) if self.provider == "gemini" else self._claude(items))
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
                          flags=list(dict.fromkeys(it.flags)), era=it.era, why=(it.why or "").strip()[:80], model=self.model)
            self.verdicts[f.id] = v
            await cache.kv_set("inspect", self._key(f), v.model_dump())

    async def finish(self, timeout: float) -> None:
        pending = [t for t in self.tasks if not t.done()]
        if not pending:
            return
        done, still = await asyncio.wait(pending, timeout=timeout)
        for t in still:
            t.cancel()

    def stats(self) -> dict:
        return {"provider": self.provider, "model": self.model, "photos": len(self.verdicts), "submitted": len(self.submitted),
                "cached": self.cached, "calls": self.calls, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "ms": self.ms, "errors": self.errors, "reference": self.ref_b64 is not None}

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
        parts: list[dict] = [{"text": self._prompt(items)}]
        if self.ref_b64:
            parts += [{"text": "R:"}, {"inline_data": {"mime_type": "image/jpeg", "data": self.ref_b64}}]
        for n, f in enumerate(items, 1):
            parts += [{"text": f"{n}:"}, {"inline_data": {"mime_type": "image/jpeg", "data": jpeg_b64(f.image)}}]
        gen: dict = {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.1,
                     "mediaResolution": "MEDIA_RESOLUTION_LOW"}
        if self.model.startswith("gemini-3"):
            gen["thinkingConfig"] = {"thinkingLevel": "minimal"}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        r = None
        for attempt in range(3):
            r = await http.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key},
                                timeout=settings.inspect_timeout_s)
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
