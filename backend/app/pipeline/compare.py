"""Compare two universities: fact sheets → pros/cons (LLM or rules) and a grounded chat.

The LLM only ever sees the two fact sheets; the system prompt forbids facts from outside them.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from pydantic import BaseModel, Field

from .. import cache, http
from ..config import settings
from ..models import CATEGORIES, CATEGORY_LABELS, Profile

log = logging.getLogger("campuslens.compare")


class Point(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list, description="Ключи фактов, на которых основан пункт, например climate.winter, budget, coverage.dormitory")


class Comparison(BaseModel):
    pros_a: list[Point]
    pros_b: list[Point]
    watch_out: list[Point]
    summary: str


async def fact_sheet(p: Profile) -> dict:
    u = p.university
    qid = u.qid
    climate = await cache.kv_get("climate", qid)
    ctx = await cache.kv_get("context", qid)
    cost = None
    if u.city_qid:
        try:
            from ..main import cost as cost_endpoint  # lazy to avoid a cycle
            cost = await cost_endpoint(u.city_qid)
        except Exception:
            cost = None
    sheet = {
        "qid": qid, "name": u.name, "name_en": u.names.get("en"), "city": u.city, "country": u.country,
        "founded": u.founded, "students": u.students, "website": u.website,
        "coverage": {c: {"verified": p.categories[c].verified, "likely": p.categories[c].likely} for c in CATEGORIES if c in p.categories},
        "coverage_overall": p.coverage.get("overall"),
        "photos_verified_total": sum(p.categories[c].verified for c in p.categories),
        "description": " ".join(s.text for s in (p.description.sentences if p.description else [])),
    }
    if climate:
        sheet["climate"] = {"year": climate["year"], "annual_t_mean": climate["annual"]["t_mean"],
                            "winter": climate["seasons"]["winter"], "summer": climate["seasons"]["summer"],
                            "comfort_days": climate["comfort"], "sun_hours": climate["annual"].get("sun_hours")}
    if ctx:
        sheet["city_context"] = {"distance_to_center_km": ctx["route_center"].get("distance_km"), "drive_min": ctx["route_center"].get("drive_min"),
                                 "stops_800m": ctx["transit"]["stops_800m"], "airport": ctx.get("airport"),
                                 "poi": {g["kind"]: g["count"] for g in ctx.get("poi", [])}, "population": ctx["center"].get("population")}
    if cost:
        sheet["budget"] = {"currency": cost["currency"], "as_of": cost["as_of"], **cost["items"],
                           "total_with_dorm": cost["total_student_month_dorm"], "total_with_rent": cost["total_student_month_rent"]}
    return sheet


def _rules(a: dict, b: dict, prefs: dict) -> Comparison:
    pa, pb, watch = [], [], []
    def cmp(key_path, label, higher_better, fmt=lambda v: str(v)):
        va, vb = a, b
        for k in key_path:
            va = va.get(k) if isinstance(va, dict) else None
            vb = vb.get(k) if isinstance(vb, dict) else None
        if va is None or vb is None:
            return
        if va == vb:
            return
        winner = pa if (va > vb) == higher_better else pb
        wv, lv = (va, vb) if winner is pa else (vb, va)
        winner.append(Point(text=f"{label}: {fmt(wv)} против {fmt(lv)}", refs=[".".join(key_path)]))
    cmp(["photos_verified_total"], "Больше подтверждённых фото", True)
    cmp(["coverage", "dormitory", "verified"], "Подтверждённые фото общежитий", True)
    cmp(["students"], "Больше студентов", True, lambda v: f"{v:,}".replace(",", " "))
    cmp(["founded"], "Старше", False)
    cmp(["climate", "winter", "t_mean"], "Теплее зимой", True, lambda v: f"{v:+.0f} °C")
    cmp(["climate", "summer", "t_max"], "Прохладнее летом", False, lambda v: f"{v:+.0f} °C")
    cmp(["climate", "comfort_days", "comfortable"], "Больше комфортных дней в году", True)
    cmp(["city_context", "distance_to_center_km"], "Ближе к центру города", False, lambda v: f"{v} км")
    cmp(["city_context", "stops_800m"], "Больше остановок рядом", True)
    cmp(["budget", "total_with_dorm"], "Дешевле месяц с общежитием", False, lambda v: f"{v:,}".replace(",", " "))
    for s, name in ((a, "a"), (b, "b")):
        if "climate" not in s:
            watch.append(Point(text=f"Для {s['name']} климат ещё не загружен — откройте вкладку «Климат»", refs=[f"{name}.climate"]))
        if "budget" not in s:
            watch.append(Point(text=f"Для города {s.get('city') or '—'} нет курируемых данных о стоимости жизни", refs=[f"{name}.budget"]))
        weak = [CATEGORY_LABELS[c]["ru"].lower() for c in CATEGORIES if c != "city" and s["coverage"].get(c, {}).get("verified", 0) == 0]
        if weak:
            watch.append(Point(text=f"{s['name']}: нет подтверждённых фото по категориям {', '.join(weak)}", refs=[f"{name}.coverage"]))
    summary = f"Сравнение по фактам профилей: у {a['name']} {len(pa)} преимуществ, у {b['name']} {len(pb)}. Учитывайте, что часть данных может отсутствовать."
    return Comparison(pros_a=pa[:6], pros_b=pb[:6], watch_out=watch[:6], summary=summary)


SYSTEM = (
    "Ты помощник абитуриента. Тебе даны два JSON с проверенными фактами о двух университетах (фото-покрытие, "
    "климат, город, бюджет). Отвечай ТОЛЬКО на основе этих фактов; если данных нет, прямо скажи об этом. "
    "Никаких гарантий поступления, рейтингов и фактов извне. Пиши по-русски, кратко, с числами."
)


async def ai_compare(a: dict, b: dict, prefs: dict) -> tuple[Comparison, str]:
    provider = settings.active_llm()
    if provider == "none":
        return _rules(a, b, prefs), "rules"
    prompt = (f"Предпочтения пользователя: {json.dumps(prefs, ensure_ascii=False)}.\n"
              f"Университет A:\n{json.dumps(a, ensure_ascii=False)}\n\nУниверситет B:\n{json.dumps(b, ensure_ascii=False)}\n\n"
              "Составь плюсы A, плюсы B (по 3–5 пунктов, каждый со ссылкой на ключ факта), «на что обратить внимание» "
              "(пропуски данных, слабое покрытие фото) и итог в 2 предложениях.")
    try:
        if provider == "claude":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key).with_options(timeout=20.0, max_retries=0)
            resp = await client.messages.parse(model=settings.claude_model, max_tokens=2000, system=SYSTEM,
                                               output_config={"effort": "low"}, messages=[{"role": "user", "content": prompt}],
                                               output_format=Comparison)
            return resp.parsed_output, f"claude/{settings.claude_model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
        schema = {"type": "OBJECT", "properties": {
            **{k: {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "refs": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["text", "refs"]}} for k in ("pros_a", "pros_b", "watch_out")},
            "summary": {"type": "STRING"}}, "required": ["pros_a", "pros_b", "watch_out", "summary"]}
        r = await http.post(url, json={"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                       "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0.2}},
                            headers={"x-goog-api-key": settings.gemini_api_key}, timeout=20.0)
        r.raise_for_status()
        return Comparison.model_validate_json(r.json()["candidates"][0]["content"]["parts"][0]["text"]), f"gemini/{settings.gemini_model}"
    except Exception as e:  # noqa: BLE001
        log.warning("ai compare failed, using rules: %s", e)
        return _rules(a, b, prefs), "rules"


async def chat(a: dict, b: dict, prefs: dict, messages: list[dict]) -> AsyncIterator[str]:
    """Yields text chunks. Without an LLM key yields one explanatory message."""
    provider = settings.active_llm()
    context = f"Предпочтения: {json.dumps(prefs, ensure_ascii=False)}\nA:\n{json.dumps(a, ensure_ascii=False)}\nB:\n{json.dumps(b, ensure_ascii=False)}"
    if provider == "none":
        yield ("Чат работает, когда настроен ключ Gemini (бесплатно) или Claude. Пока доступно сравнение по правилам выше: "
               "оно использует те же проверенные факты профилей.")
        return
    if provider == "claude":
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key).with_options(timeout=30.0, max_retries=0)
        msgs = [{"role": "user", "content": context + "\n\n" + messages[0]["content"]}] + [{"role": m["role"], "content": m["content"]} for m in messages[1:]]
        async with client.messages.stream(model=settings.claude_model, max_tokens=1500, system=SYSTEM,
                                          output_config={"effort": "low"}, messages=msgs) as stream:
            async for text in stream.text_stream:
                yield text
        return
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    contents = [{"role": "user", "parts": [{"text": context}]}, {"role": "model", "parts": [{"text": "Понял. Отвечаю только по этим данным."}]}]
    for m in messages:
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]})
    r = await http.post(url, json={"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents, "generationConfig": {"temperature": 0.3}},
                        headers={"x-goog-api-key": settings.gemini_api_key}, timeout=30.0)
    r.raise_for_status()
    yield r.json()["candidates"][0]["content"]["parts"][0]["text"]
