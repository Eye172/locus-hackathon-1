"""Judge agent: a vision LLM gives a second opinion on borderline photos (confidence 0.30–0.65).

Providers: Gemini (REST, JSON schema) or Claude (messages.parse with an image block). Verdicts are cached by
SHA-1 of the image so the same photo is never judged twice, and the free Gemini tier (10 RPM) is respected by
judging at most `judge_max_photos` per profile, in parallel batches of 4.
"""
from __future__ import annotations

import asyncio
import time
import base64
import io
import logging

from pydantic import BaseModel, Field

from .. import cache, http
from ..config import settings
from ..models import CATEGORIES, Photo, Signal
from .fetch import Fetched

log = logging.getLogger("campuslens.judge")


class Verdict(BaseModel):
    is_campus_photo: bool = Field(description="Является ли изображение фотографией университетского кампуса, общежития, аудитории, библиотеки, лаборатории, спортобъекта, студенческого мероприятия или города")
    category: str = Field(description="Одно из: campus, dormitory, classroom, library, lab, sports, student_life, city, none")
    reason: str = Field(description="Одно короткое предложение на русском")


PROMPT = (
    "Ты проверяешь фотографии для профиля университета «{name}» ({city}). Ответь строго по схеме: "
    "является ли изображение реальной фотографией кампуса/общежития/аудитории/библиотеки/лаборатории/спорта/"
    "студенческой жизни или города (не логотип, не карта, не документ, не постер, не портрет); к какой категории "
    "относится; одно предложение с причиной. Подпись к файлу: «{title}»."
)


def _jpeg_b64(f: Fetched) -> str:
    buf = io.BytesIO()
    im = f.image.copy()
    im.thumbnail((512, 512))
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def _gemini(f: Fetched, name: str, city: str | None) -> Verdict | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": PROMPT.format(name=name, city=city or "", title=f.cand.title or "")},
            {"inline_data": {"mime_type": "image/jpeg", "data": _jpeg_b64(f)}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1, "responseSchema": {
            "type": "OBJECT", "properties": {"is_campus_photo": {"type": "BOOLEAN"}, "category": {"type": "STRING"}, "reason": {"type": "STRING"}},
            "required": ["is_campus_photo", "category", "reason"]}},
    }
    r = await http.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=12.0)
    r.raise_for_status()
    return Verdict.model_validate_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])


async def _claude(f: Fetched, name: str, city: str | None) -> Verdict | None:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key).with_options(timeout=12.0, max_retries=0)
    resp = await client.messages.parse(
        model=settings.claude_model, max_tokens=400, output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(f)}},
            {"type": "text", "text": PROMPT.format(name=name, city=city or "", title=f.cand.title or "")},
        ]}],
        output_format=Verdict,
    )
    return resp.parsed_output


async def review(f: Fetched, name: str, city: str | None) -> tuple[Verdict | None, str]:
    provider = settings.active_llm()
    if provider == "none":
        return None, "none"
    cached = await cache.kv_get("judge", f.sha1 or f.id)
    if cached:
        return Verdict.model_validate(cached["verdict"]), cached.get("provider", provider)
    try:
        v = await (_gemini(f, name, city) if provider == "gemini" else _claude(f, name, city))
    except Exception as e:  # noqa: BLE001
        log.warning("judge failed for %s: %s", f.id, e)
        return None, provider
    if v:
        await cache.kv_set("judge", f.sha1 or f.id, {"verdict": v.model_dump(), "provider": provider})
    return v, provider


def apply(p: Photo, v: Verdict, provider: str) -> None:
    """Adjust confidence and category from the verdict; the adjustment is shown as a signal."""
    if v.is_campus_photo:
        p.signals.append(Signal(key="judge_yes", label=f"Vision-судья ({provider}) подтверждает фото", weight=0.15, value=v.reason))
        if v.category in CATEGORIES and v.category != p.category and p.category_scores.get(v.category, 0) >= 0.1:
            p.secondary = p.category
            p.category = v.category  # type: ignore[assignment]
    else:
        p.signals.append(Signal(key="judge_no", label=f"Vision-судья ({provider}) не считает это фото кампуса", weight=-0.25, value=v.reason))
    conf = max(0.0, min(1.0, sum(s.weight for s in p.signals)))
    p.confidence = round(conf, 3)
    p.level = "verified" if conf >= settings.verified_threshold else "likely" if conf >= settings.likely_cut() else "unverified"
    if p.level == "unverified":
        p.rejected, p.reject_reason = True, f"низкая уверенность ({p.confidence:.0%}) после проверки vision-судьёй: {v.reason}"
    elif p.rejected and p.reject_reason and p.reject_reason.startswith("низкая уверенность"):
        p.rejected, p.reject_reason = False, None


async def run(photos: list[Photo], fetched_by_id: dict[str, Fetched], name: str, city: str | None,
              budget_s: float = 8.0) -> int:
    """Judge borderline photos in place, most confident first, applying each batch as it lands so a tight budget
    still yields partial verdicts. Returns the number of verdicts applied."""
    if settings.active_llm() == "none":
        return 0
    borderline = [p for p in photos if settings.judge_low <= p.confidence < settings.judge_high and p.id in fetched_by_id]
    borderline.sort(key=lambda p: -p.confidence)
    borderline = borderline[: settings.judge_max_photos]
    applied = 0
    deadline = time.monotonic() + budget_s
    for i in range(0, len(borderline), 5):
        left = deadline - time.monotonic()
        if left < 1.5:
            break
        batch = borderline[i:i + 5]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[review(fetched_by_id[p.id], name, city) for p in batch], return_exceptions=True), timeout=left)
        except asyncio.TimeoutError:
            break
        for p, res in zip(batch, results):
            if isinstance(res, tuple) and res[0] is not None:
                apply(p, res[0], res[1])
                applied += 1
    return applied
