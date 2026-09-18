"""Search settings and the campus facts: /api/search-plan, /api/search-plan/{qid}, /api/facts/{qid}."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import cache
from .models import University
from .pipeline import campus_facts
from .pipeline import search_plan as sp

router = APIRouter()


async def _university(qid: str) -> University:
    p = await cache.get_profile(qid)
    if p:
        return p.university
    from .pipeline import enrich
    try:
        uni, _ = await enrich.facts(qid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"вуз не найден: {e}") from e
    return uni


@router.get("/api/search-plan")
async def get_plan():
    return {"plan": sp.to_json(sp.load()), "defaults": sp.to_json(sp.default_plan()), "platforms": sp.PLATFORMS}


@router.put("/api/search-plan")
async def put_plan(plan: sp.Plan):
    try:
        return {"plan": sp.to_json(sp.save(plan))}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/search-plan/reset")
async def reset_plan():
    return {"plan": sp.to_json(sp.reset())}


@router.get("/api/search-plan/{qid}")
async def get_uni_plan(qid: str):
    """This university's own additions, and the queries a build of it would send - per theme and network."""
    uni = await _university(qid)
    up = await sp.load_uni(qid)
    plan = await sp.for_university(qid)
    from .pipeline.langs import local_lang
    local = local_lang(uni)

    def sent(i: sp.Intent, pl: str) -> list[str]:
        """Exactly what social_search / web_images / youtube_intents send for this theme and network."""
        qs = sp.queries(plan, i, uni, pl, up.names)
        if pl == "instagram":   # the Popular page: the first English phrase and the custom ones
            return [q for lang, q in qs if lang == "custom"] + [q for lang, q in qs if lang == "en"][:1]
        if pl == "youtube":     # one search per theme (100 quota units each), in the students' language
            q = next((q for lang, q in qs if lang == local), None) or next((q for _, q in qs), None)
            return [q] if q else []
        return [q for _, q in qs]

    preview = {i.key: {pl: sent(i, pl) for pl in i.platforms if pl != "maps"}
               | ({"maps": i.maps} if "maps" in i.platforms and i.maps else {})
               for i in plan.intents if i.enabled}
    found = await cache.kv_get("discover", qid)
    return {"qid": qid, "name": uni.names.get("ru") or uni.name, "uni": up.model_dump(), "preview": preview,
            "names": sp.name_forms(uni, up.names), "discovered": found}


@router.put("/api/search-plan/{qid}")
async def put_uni_plan(qid: str, up: sp.UniPlan):
    await sp.save_uni(qid, up)
    if up.names:
        await cache.kv_set("discover", qid, None)   # new names: look for the accounts and hashtags again
    return {"uni": up.model_dump()}


@router.get("/api/facts/{qid}")
async def facts(qid: str, refresh: bool = False):
    uni = await _university(qid)
    return await campus_facts.build(uni, refresh=refresh)
