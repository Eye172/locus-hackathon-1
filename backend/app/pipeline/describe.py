"""Campus description grounded in collected facts. Every sentence cites its sources.

Two modes: Claude (structured output, facts-only prompt) when ANTHROPIC_API_KEY is set, otherwise a
template that assembles the same facts. The UI shows which mode produced the text.
"""
from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, Field

from ..config import settings
from ..models import Campus, Description, DescriptionSource, Sentence, University, CATEGORY_LABELS

log = logging.getLogger("campuslens.describe")


class _SentenceOut(BaseModel):
    text: str = Field(description="Одно предложение на русском языке")
    sources: list[int] = Field(description="Идентификаторы источников, на которых основано предложение")


class _DescriptionOut(BaseModel):
    sentences: list[_SentenceOut]


def _sources(uni: University, campus: Campus | None) -> list[DescriptionSource]:
    src = []
    if uni.summary_url:
        src.append(DescriptionSource(id=1, label="Википедия", url=uni.summary_url))
    src.append(DescriptionSource(id=2, label="Wikidata", url=f"https://www.wikidata.org/wiki/{uni.qid}"))
    if campus and campus.osm_url:
        src.append(DescriptionSource(id=3, label="OpenStreetMap", url=campus.osm_url))
    if uni.website:
        src.append(DescriptionSource(id=4, label="Официальный сайт", url=uni.website))
    return src


def _facts(uni: University, campus: Campus | None, stats: dict, context: dict | None) -> dict:
    counts = campus.counts if campus else {}
    return {
        "name": uni.name, "name_en": uni.names.get("en"), "city": uni.city, "country": uni.country,
        "founded": uni.founded, "students": uni.students,
        "wikipedia_extract": (uni.summary or "")[:700],
        "osm": {
            "campus_outline": campus.mode if campus else None,
            "tagged_objects": sum(counts.values()),
            "dormitories": counts.get("dormitory", 0), "libraries": counts.get("library", 0),
            "sports_facilities": counts.get("sports", 0), "academic_buildings": counts.get("academic", 0),
            "cafes": counts.get("student_life", 0),
        },
        "context": {k: v for k, v in (context or {}).items() if k in ("distance_km", "center_name", "transport_stops", "climate")},
        "photos": stats,
    }


def template(uni: University, campus: Campus | None, stats: dict, context: dict | None) -> Description:
    sources = _sources(uni, campus)
    ids = {s.label: s.id for s in sources}
    sents: list[Sentence] = []
    first = None
    if uni.summary:
        first = uni.summary.split(". ")[0].strip()
        if first and not first.endswith("."):
            first += "."
        sents.append(Sentence(text=first, sources=[ids["Википедия"]]))
    facts = []
    if uni.founded:
        facts.append(f"основан в {uni.founded} году")
    if uni.students:
        facts.append(f"около {uni.students:,} студентов".replace(",", " "))
    if uni.city:
        facts.append(f"находится в городе {uni.city}")
    if facts:
        sents.append(Sentence(text=(uni.name + " " + ", ".join(facts) + ".").replace("  ", " "), sources=[ids["Wikidata"]]))
    if campus and campus.mode == "polygon":
        c = campus.counts
        parts = []
        if c.get("dormitory"):
            parts.append(f"{c['dormitory']} общежитий")
        if c.get("library"):
            parts.append(f"{c['library']} библиотек")
        if c.get("sports"):
            parts.append(f"{c['sports']} спортивных объектов")
        if c.get("academic"):
            parts.append(f"{c['academic']} учебных корпусов")
        if parts:
            sents.append(Sentence(text=f"В границах кампуса на карте OpenStreetMap отмечено {', '.join(parts)}.",
                                  sources=[ids["OpenStreetMap"]]))
    elif campus:
        sents.append(Sentence(text="Границы кампуса в OpenStreetMap не найдены, поэтому проверка фото по геометке ведётся в радиусе 500 м от координат вуза.",
                              sources=[ids["Wikidata"]]))
    if context and context.get("distance_km") is not None:
        sents.append(Sentence(text=f"Расстояние от кампуса до центра города — около {context['distance_km']:.1f} км.",
                              sources=sorted({ids["Wikidata"], ids.get("OpenStreetMap", ids["Wikidata"])})))
    verified = stats.get("verified", 0)
    weak = [CATEGORY_LABELS[c]["ru"].lower() for c in stats.get("weak", [])]
    if verified:
        t = f"Найдено {verified} подтверждённых фотографий в {stats.get('sources', 0)} источниках"
        t += f"; слабое покрытие: {', '.join(weak)}." if weak else "."
        sents.append(Sentence(text=t, sources=[]))
    else:
        sents.append(Sentence(text="Подтверждённых фотографий пока не найдено: данных в открытых источниках мало.", sources=[]))
    note = None if uni.summary else "Статья в Википедии не найдена, описание собрано только из структурированных данных."
    return Description(mode="template", sentences=sents, sources=sources, note=note)


def _prompt(uni: University, campus: Campus | None, stats: dict, context: dict | None) -> tuple[str, list[DescriptionSource]]:
    """One prompt for every provider (Claude, Gemini, or a Node-side model): facts in, cited sentences out."""
    sources = _sources(uni, campus)
    facts = _facts(uni, campus, stats, context)
    src_lines = "\n".join(f"[{s.id}] {s.label}: {s.url}" for s in sources)
    prompt = (
        "Ты пишешь краткое описание кампуса университета для абитуриента на русском языке.\n"
        "Используй ТОЛЬКО факты из JSON ниже. Ничего не добавляй от себя: ни рейтингов, ни оценок качества, "
        "ни фактов, которых нет в данных. Если данных мало, прямо напиши об этом одним предложением.\n"
        "Напиши 4–6 коротких предложений. Для каждого предложения укажи идентификаторы источников: "
        "1 — Википедия (wikipedia_extract), 2 — Wikidata (name, city, founded, students), "
        "3 — OpenStreetMap (osm, context.distance_km, context.transport_stops), 4 — официальный сайт. "
        "Предложение о фотографиях (photos) источников не требует.\n\n"
        f"Источники:\n{src_lines}\n\nФакты:\n{json.dumps(facts, ensure_ascii=False)}"
    )
    return prompt, sources


def _finish(parsed: _DescriptionOut, sources: list[DescriptionSource], provider: str) -> Description | None:
    valid_ids = {s.id for s in sources}
    sents = [Sentence(text=s.text.strip(), sources=[i for i in s.sources if i in valid_ids])
             for s in parsed.sentences if s.text.strip()]
    if not sents:
        return None
    return Description(mode="llm", sentences=sents[:6], sources=sources, note=f"provider: {provider}")


async def claude(uni: University, campus: Campus | None, stats: dict, context: dict | None, timeout: float = 9.0) -> Description | None:
    try:
        import anthropic
    except ImportError:
        return None
    prompt, sources = _prompt(uni, campus, stats, context)
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key).with_options(timeout=timeout, max_retries=0)
    try:
        resp = await asyncio.wait_for(client.messages.parse(
            model=settings.claude_model,
            max_tokens=1500,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
            output_format=_DescriptionOut,
        ), timeout=timeout + 1)
        return _finish(resp.parsed_output, sources, f"claude/{settings.claude_model}")
    except Exception as e:  # noqa: BLE001
        log.warning("Claude description failed: %s", e)
        return None


async def gemini(uni: University, campus: Campus | None, stats: dict, context: dict | None, timeout: float = 9.0) -> Description | None:
    """Gemini via REST with a JSON response schema; the reply is validated with pydantic (the Zod equivalent)."""
    from .. import http
    prompt, sources = _prompt(uni, campus, stats, context)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {"sentences": {"type": "ARRAY", "items": {
                    "type": "OBJECT",
                    "properties": {"text": {"type": "STRING"}, "sources": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
                    "required": ["text", "sources"]}}},
                "required": ["sentences"],
            },
            "temperature": 0.2,
        },
    }
    try:
        r = await http.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=timeout)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = _DescriptionOut.model_validate_json(text)
        return _finish(parsed, sources, f"gemini/{settings.gemini_model}")
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini description failed: %s", e)
        return None


async def build(uni: University, campus: Campus | None, stats: dict, context: dict | None,
                timeout: float = 9.0) -> Description:
    provider = settings.active_llm()
    d = None
    if provider == "claude":
        d = await claude(uni, campus, stats, context, timeout=timeout)
    elif provider == "gemini":
        d = await gemini(uni, campus, stats, context, timeout=timeout)
    return d or template(uni, campus, stats, context)
