"""Compare two universities: fact sheets → pros/cons (LLM or rules) and a grounded chat.

The LLM only ever sees the two fact sheets; the system prompt forbids facts from outside them.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from pydantic import BaseModel, Field

from .. import cache, http
from ..config import settings
from .ai_inspector import gemini_models, gemini_post
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
        "qid": qid, "name": u.name, "name_en": u.name_en or u.names.get("en"), "city": u.city, "country": u.country,
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
    "Никаких гарантий поступления, рейтингов и фактов извне. Пиши по-русски, кратко, с числами. "
    "В тексте название университета пиши ровно так, как в поле name: не переводи его на русский и не склоняй. "
    "В refs — только ключи фактов (climate.annual_t_mean, coverage.dormitory), без названия университета."
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
        schema = {"type": "OBJECT", "properties": {
            **{k: {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "refs": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["text", "refs"]}} for k in ("pros_a", "pros_b", "watch_out")},
            "summary": {"type": "STRING"}}, "required": ["pros_a", "pros_b", "watch_out", "summary"]}
        j, model = await gemini_post({"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                      "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0.2}},
                                     20.0)
        return Comparison.model_validate_json(j["candidates"][0]["content"]["parts"][0]["text"]), f"gemini/{model}"
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model()}:generateContent"
    contents = [{"role": "user", "parts": [{"text": context}]}, {"role": "model", "parts": [{"text": "Понял. Отвечаю только по этим данным."}]}]
    for m in messages:
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]})
    r = await http.post(url, json={"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents, "generationConfig": {"temperature": 0.3}},
                        headers={"x-goog-api-key": settings.gemini_api_key}, timeout=30.0)
    r.raise_for_status()
    yield r.json()["candidates"][0]["content"]["parts"][0]["text"]


def gemini_model() -> str:
    """The first model whose daily quota is not spent (the inspector keeps that list)."""
    return (gemini_models() or [settings.gemini_model])[0]


# ---------- advisor: one long chat about two universities (the compare page) ----------
ADVISOR_VERSION = 2
LANG_NAME = {"ru": "русском", "en": "английском", "kk": "казахском"}
ADVISOR_SYSTEM = """Ты — спокойный и честный консультант абитуриента. Ты помогаешь сравнить два университета по данным, которые тебе дали в JSON (A и B).
Правила:
- Опирайся только на эти данные. Чего в них нет — прямо скажи «в данных этого нет» и не выдумывай: рейтинги, стоимость обучения, программы, гранты — только если это есть в тексте Википедии или справке о кампусе.
- Сколько фото нашлось о вузе и насколько полон его профиль — не показатель качества; никогда не используй это как довод.
- Не выноси окончательный вердикт и не говори «выбирайте X». Показывай, что в каком вузе совпадает с предпочтениями человека, что не совпадает и чем придётся поступиться. Можно мягко подсказать, на что посмотреть внимательнее.
- Пиши на {lang} языке, живо и конкретно, с числами из данных (температуры, расстояния, деньги с валютой). Название вуза пиши ровно как в поле name.
- Формат — Markdown: короткие заголовки «### », списки «- », **жирный** для главного слова пункта. Без таблиц, эмодзи и приветствий. Не пересказывай данные целиком — выбирай то, что важно для выбора.
- Пробелы в данных — не слабая сторона вуза. Не записывай их в минусы; если важного нет, скажи об этом одной строкой."""
ADVISOR_INTRO = """Напиши вступительное сообщение сравнения — сразу по делу, без приветствия. Строго в таком виде:

### <name A>
**Сильные стороны**
- **<тема>** — <конкретно, с цифрами>
(3–5 пунктов)
**Слабые стороны**
- **<тема>** — <конкретно, с цифрами>
(2–4 пункта; только реальные минусы для студента, не пробелы в данных)

### <name B>
(так же)

### Главные различия
- **<тема>** — <чем A отличается от B> (3–5 пунктов: климат, город и дорога до центра, жильё и что рядом с кампусом, учёба и масштаб вуза, язык обучения — что есть в данных)

**Чего нет в данных:** <одна строка, если важного нет, например стоимости обучения>

В конце одной фразой спроси, что для человека важно (бюджет, климат, большой город или тихий кампус, общежитие, спорт, специальность…), чтобы подсказать точнее."""


async def _wiki_intro(u, lang: str) -> str:
    """The Wikipedia lead of the university (plain text, <= 2500 chars), in the UI language when there is an
    article, else in English; cached for 30 days."""
    for lg in dict.fromkeys([lang, "en", "ru"]):
        title = (u.wikipedia or {}).get(lg)
        if not title:
            continue
        key = f"{u.qid}:{lg}"
        hit = await cache.kv_get("wiki_intro", key, max_age_s=30 * 86400)
        if hit is not None:
            if hit.get("text"):
                return hit["text"]
            continue
        try:
            j = await http.get_json(f"https://{lg}.wikipedia.org/w/api.php", params={
                "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1,
                "titles": title, "format": "json"}, timeout=6.0)
            pages = (j.get("query") or {}).get("pages") or {}
            text = (next(iter(pages.values()), {}).get("extract") or "") if pages else ""
        except Exception as e:  # noqa: BLE001
            log.info("wiki intro %s failed: %r", key, e)
            continue
        text = text.strip()[:2500]
        await cache.kv_set("wiki_intro", key, {"text": text})
        if text:
            return text
    return ""


async def advisor_sheet(p: Profile, lang: str = "ru") -> dict:
    """Everything the advisor may use about one university. Loads the climate year and the city context when they
    are missing (short timeouts); photo counts and profile coverage are left out on purpose."""
    from ..main import _climate_pack, context as context_endpoint  # lazy: main imports this module
    u = p.university
    try:
        await asyncio.wait_for(_climate_pack(u.qid), timeout=40)
    except Exception as e:  # noqa: BLE001
        log.info("advisor climate %s: %r", u.qid, e)
    try:
        await asyncio.wait_for(context_endpoint(u.qid), timeout=5)
    except Exception as e:  # noqa: BLE001
        log.info("advisor context %s: %r", u.qid, e)
    base = await fact_sheet(p)
    sheet = {k: v for k, v in base.items() if k not in ("coverage", "coverage_overall", "photos_verified_total")}
    climate = await cache.kv_get("climate", u.qid)
    if climate:
        seasons = {k: {f: s.get(f) for f in ("t_mean", "t_hi", "t_lo", "feels", "precip_days", "snow_days", "sun_h_day", "wind_ms")}
                   for k, s in climate["seasons"].items()}
        sheet["climate"] = {"year": climate["year"], "annual_t_mean": climate["annual"]["t_mean"], "seasons": seasons,
                            "comfort_days": climate["comfort"], "sun_hours": climate["annual"].get("sun_hours"),
                            "hemisphere": climate.get("hemisphere", "north")}
    wiki = await _wiki_intro(u, lang)
    if wiki:
        sheet["wikipedia_intro"] = wiki
    facts = await cache.kv_get("facts", u.qid)
    if facts and (facts.get("summary") or facts.get("sections")):
        sheet["campus_facts"] = {"summary": facts.get("summary", ""),
                                 "sections": [{"title": s.get("title"), "rating": s.get("rating"),
                                               "items": [i.get("text") for i in s.get("items", [])][:6]} for s in facts.get("sections", [])],
                                 "quick": [f"{q.get('label')}: {q.get('value')}" for q in facts.get("quick", [])]}
    if p.campus and p.campus.counts:
        sheet["campus_osm"] = {"mode": p.campus.mode, "buildings_by_kind": p.campus.counts}
    m3 = await cache.kv_get("map3d", f"{u.qid}:ru") or await cache.kv_get("map3d", f"{u.qid}:en")
    if not m3:
        from ..main import map3d_pack
        try:
            m3 = await asyncio.wait_for(map3d_pack(u.qid, False, "ru"), timeout=20)
        except Exception as e:  # noqa: BLE001
            log.info("advisor map3d %s: %r", u.qid, e)
    if m3:
        sheet["around_campus"] = _around(m3)
    return sheet


OWNERSHIP = {"campus": "на территории кампуса", "name": "вуза (по названию)", "unknown": "чьё — не указано"}


def _around(m3: dict) -> dict:
    """What is around the campus, from the 3D map pack (OSM + Google): dorms, places by kind, the city centre."""
    out: dict = {}
    dorms = sorted(m3.get("dorms") or [], key=lambda d: d.get("distance_m") or 1e9)
    if dorms:
        out["dorms_nearby"] = {"count": len(dorms), "nearest": [
            {"name": d.get("name") or "без названия", "distance_m": d.get("distance_m"), "whose": OWNERSHIP.get(d.get("ownership"), "")}
            for d in dorms[:5]]}
    places = {}
    for kind, items in (m3.get("places") or {}).items():
        if not items:
            continue
        near = sum(1 for p in items if (p.get("distance_m") or 1e9) <= 1000)
        nearest = min(items, key=lambda p: p.get("distance_m") or 1e9)
        places[kind] = {"within_1km": f"{near}+" if near == len(items) >= 40 else near,
                        "nearest": f"{nearest.get('name') or kind}, {nearest.get('distance_m')} м"}
    if places:
        out["places_by_kind"] = places   # cafe, food, shops, sport, park, culture, fun, health, transit
    c, r = m3.get("center"), m3.get("route_center") or {}
    if c:
        out["city_center"] = {"name": c.get("name"), "distance_km": c.get("distance_km"),
                              "walk_min": r.get("walk_min"), "drive_min": r.get("drive_min")}
    camp = m3.get("campus") or {}
    if camp.get("area_ha"):
        out["campus_area_ha"] = camp["area_ha"]
    if camp.get("buildings"):
        out["campus_buildings_osm"] = len(camp["buildings"])
    return out


def _contents(a: dict, b: dict, messages: list[dict]) -> list[dict]:
    data = f"Университет A:\n{json.dumps(a, ensure_ascii=False)}\n\nУниверситет B:\n{json.dumps(b, ensure_ascii=False)}"
    contents = [{"role": "user", "parts": [{"text": data + "\n\n" + ADVISOR_INTRO}]}]
    for m in messages:
        text = str(m.get("content", "")).strip()[:4000]
        if not text:
            continue
        role = "model" if m.get("role") == "assistant" else "user"
        if contents[-1]["role"] == role:           # keep the turns alternating
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    return contents


async def advisor_stream(a: dict, b: dict, messages: list[dict], lang: str = "ru") -> AsyncIterator[str]:
    """Streams the advisor's answer (the intro when `messages` is empty): Vertex / AI Studio streamGenerateContent,
    falling back to one non-streamed call."""
    from .ai_inspector import _endpoint, gemini_routes
    body = {"systemInstruction": {"parts": [{"text": ADVISOR_SYSTEM.format(lang=LANG_NAME.get(lang, "русском"))}]},
            "contents": _contents(a, b, messages), "generationConfig": {"temperature": 0.5, "maxOutputTokens": 2500}}
    for k, key, model in gemini_routes():
        try:
            url, hdrs = await _endpoint(k, key, model)
        except Exception:  # noqa: BLE001
            continue
        url = url.replace(":generateContent", ":streamGenerateContent") + "?alt=sse"
        sent = False
        try:
            async with http.client().stream("POST", url, json=body, headers=hdrs, timeout=60.0) as r:
                if r.status_code != 200:
                    log.info("advisor stream %s/%s -> %s", k, model, r.status_code)
                    continue
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    for part in (((chunk.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []):
                        if part.get("text") and not part.get("thought"):
                            sent = True
                            yield part["text"]
            if sent:
                return
        except Exception as e:  # noqa: BLE001
            log.info("advisor stream %s/%s failed: %r", k, model, e)
            if sent:
                return
    j, _ = await gemini_post(body, 60.0)   # no route streamed: one plain call (raises when every quota is spent)
    yield "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought"))
