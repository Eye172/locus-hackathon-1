"""Wikidata: entity resolution ("did you mean"), aliases in three languages, facts, links to other sources."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

from ... import http
from ...models import Candidate, University

API = "https://www.wikidata.org/w/api.php"
LANGS = ["ru", "en", "kk"]

# Classes that count as a university-like institution (P31). Not exhaustive; a description regex backs it up.
UNIVERSITY_CLASSES = {
    "Q3918", "Q875538", "Q265662", "Q902104", "Q1371037", "Q15936437", "Q38723", "Q23002054",
    "Q189004", "Q4671277", "Q1663017", "Q62078547", "Q1321960", "Q2120466", "Q1143635", "Q4287745",
    "Q1500306", "Q3592011", "Q7075", "Q2385804", "Q5341295", "Q1244442", "Q1321932", "Q31855",
    "Q5155040", "Q3354859", "Q1517299", "Q124573118", "Q45400320", "Q7315155", "Q4830453",
}
# Never a university, whatever the description says: humans, settlements, asteroids, films, disambiguation pages…
EXCLUDED_CLASSES = {
    "Q5", "Q515", "Q486972", "Q3863", "Q4167410", "Q7725634", "Q5398426", "Q11424", "Q13442814", "Q1002697",
    "Q1093829", "Q532", "Q3957", "Q15284", "Q2775969", "Q23413", "Q4830453", "Q16970", "Q1248784", "Q12089225",
}
UNI_RE = re.compile(
    r"универ|university|universit|институт|institute|college|колледж|academy|академи|высш|higher education|"
    r"business school|polytechnic|политехн|техническ|technical|hochschule|universidad|université|università|"
    r"universität|escuela|школа|мектеб|жоғары оқу|conservator|консерватор",
    re.I,
)


def _first(claims: dict, prop: str):
    for c in claims.get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        return snak["datavalue"]["value"]
    return None


def _all(claims: dict, prop: str) -> list:
    out = []
    for c in claims.get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append((c.get("rank", "normal"), snak["datavalue"]["value"]))
    # preferred rank first
    out.sort(key=lambda x: 0 if x[0] == "preferred" else 1)
    return [v for _, v in out]


def commons_file_url(filename: str, width: int = 800) -> str:
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename.replace(' ', '_'))}?width={width}"


def is_university(entity: dict) -> bool:
    claims = entity.get("claims", {})
    p31: list[str] = []
    for c in claims.get("P31", []):
        try:
            p31.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except KeyError:
            continue
    if any(x in EXCLUDED_CLASSES for x in p31):
        return False
    if any(x in UNIVERSITY_CLASSES for x in p31):
        return True
    descs = entity.get("descriptions", {})
    labels = entity.get("labels", {})
    text = " ".join(v["value"] for v in list(descs.values()) + list(labels.values()))
    return bool(UNI_RE.search(text))


async def _search_lang(q: str, lang: str, limit: int = 7) -> list[dict]:
    try:
        d = await http.get_json(API, params={
            "action": "wbsearchentities", "search": q, "language": lang, "uselang": lang,
            "type": "item", "limit": limit, "format": "json",
        }, timeout=4.0)
        return d.get("search", [])
    except Exception:
        return []


async def get_entities(ids: list[str], props: str = "labels|descriptions|claims", languages: str = "ru|en|kk") -> dict:
    if not ids:
        return {}
    d = await http.get_json(API, params={
        "action": "wbgetentities", "ids": "|".join(ids[:50]), "props": props,
        "languages": languages, "format": "json",
    }, timeout=6.0)
    return d.get("entities", {})


def _label(entity: dict, langs=LANGS) -> str | None:
    labels = entity.get("labels", {})
    for l in langs:
        if l in labels:
            return labels[l]["value"]
    if labels:
        return next(iter(labels.values()))["value"]
    return None


def _desc(entity: dict, langs=LANGS) -> str | None:
    descs = entity.get("descriptions", {})
    for l in langs:
        if l in descs:
            return descs[l]["value"]
    return None


async def search(q: str) -> list[Candidate]:
    """Search Wikidata in three languages, keep only university-like items, attach city/country labels."""
    results = await asyncio.gather(*[_search_lang(q, l) for l in LANGS])
    order: list[str] = []
    rank: dict[str, float] = {}
    for res in results:
        for i, r in enumerate(res):
            qid = r["id"]
            score = 1.0 / (i + 1)
            if qid not in rank:
                order.append(qid)
                rank[qid] = score
            else:
                rank[qid] = max(rank[qid], score)
    if not order:
        return []
    entities = await get_entities(order[:15])
    cands: list[tuple[str, dict]] = [(qid, e) for qid, e in entities.items() if "missing" not in e and is_university(e)]
    # collect city/country ids for labels
    ref_ids: set[str] = set()
    for _, e in cands:
        for prop in ("P131", "P17"):
            v = _first(e.get("claims", {}), prop)
            if v and isinstance(v, dict) and v.get("id"):
                ref_ids.add(v["id"])
    refs = await get_entities(sorted(ref_ids), props="labels") if ref_ids else {}
    out: list[Candidate] = []
    for qid, e in cands:
        claims = e.get("claims", {})
        city = _first(claims, "P131")
        country = _first(claims, "P17")
        logo = _first(claims, "P154") or _first(claims, "P18")
        out.append(Candidate(
            qid=qid,
            label=_label(e) or qid,
            description=_desc(e),
            city=_label(refs.get(city["id"], {})) if city else None,
            country=_label(refs.get(country["id"], {})) if country else None,
            logo_url=commons_file_url(logo, 160) if isinstance(logo, str) else None,
            score=rank.get(qid, 0.0),
            origin="wikidata",
        ))
    out.sort(key=lambda c: -c.score)
    return out


async def entity(qid: str) -> University:
    ents = await get_entities([qid], props="labels|aliases|descriptions|claims|sitelinks")
    e = ents.get(qid)
    if not e or "missing" in e:
        raise ValueError(f"Wikidata item {qid} not found")
    claims = e.get("claims", {})
    labels = {l: v["value"] for l, v in e.get("labels", {}).items() if l in LANGS}
    aliases: list[str] = []
    for l in LANGS:
        aliases += [a["value"] for a in e.get("aliases", {}).get(l, [])]
    for v in labels.values():
        if v not in aliases:
            aliases.append(v)
    sitelinks = {}
    for l in LANGS:
        sl = e.get("sitelinks", {}).get(f"{l}wiki")
        if sl:
            sitelinks[l] = sl["title"]

    coord = _first(claims, "P625")
    websites = [w for w in _all(claims, "P856") if isinstance(w, str)]
    website = next((w for w in websites if w.startswith("https")), websites[0] if websites else None)
    inception = _first(claims, "P571")
    founded = None
    if inception and isinstance(inception, dict):
        m = re.match(r"[+-]?(\d{1,4})", inception.get("time", ""))
        if m:
            founded = int(m.group(1))
    students_v = _first(claims, "P2196")
    students = None
    if students_v and isinstance(students_v, dict):
        try:
            students = int(float(students_v.get("amount", "0")))
        except ValueError:
            students = None
    logo = _first(claims, "P154")
    image = _first(claims, "P18")
    city = _first(claims, "P131")
    country = _first(claims, "P17")

    uni = University(
        qid=qid,
        name=labels.get("ru") or labels.get("en") or labels.get("kk") or qid,
        names=labels,
        aliases=aliases,
        description=_desc(e),
        website=website,
        commons_category=_first(claims, "P373"),
        wikipedia=sitelinks,
        lat=coord.get("latitude") if coord else None,
        lon=coord.get("longitude") if coord else None,
        coord_source="wikidata" if coord else None,
        city_qid=city["id"] if city else None,
        founded=founded,
        students=students,
        logo_url=commons_file_url(logo, 200) if isinstance(logo, str) else None,
        image_url=commons_file_url(image, 800) if isinstance(image, str) else None,
    )

    ref_ids = [x for x in [uni.city_qid, country["id"] if country else None] if x]
    if ref_ids:
        refs = await get_entities(ref_ids, props="labels|claims|sitelinks")
        if uni.city_qid and uni.city_qid in refs:
            c = refs[uni.city_qid]
            uni.city = _label(c)
            cc = _first(c.get("claims", {}), "P625")
            if cc:
                uni.city_lat, uni.city_lon = cc.get("latitude"), cc.get("longitude")
            uni.city_commons_category = _first(c.get("claims", {}), "P373")
            pop = _first(c.get("claims", {}), "P1082")
            if pop and isinstance(pop, dict):
                try:
                    uni.city_population = int(float(pop.get("amount", "0")))
                except ValueError:
                    pass
            for l in LANGS:
                sl = c.get("sitelinks", {}).get(f"{l}wiki")
                if sl:
                    uni.city_wikipedia[l] = sl["title"]
        if country and country["id"] in refs:
            uni.country = _label(refs[country["id"]])
    return uni
