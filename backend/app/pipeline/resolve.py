"""Name resolution: offline fuzzy index (typo-tolerant, instant) merged with live Wikidata search.

Matching tricks that matter in practice:
- generic words (university / университет / universitet / институт …) are stripped before scoring, otherwise
  every "University of X" scores 90 against "univer";
- Cyrillic names get a Latin transliteration variant, so "Satpaev universitet" hits «…имени К. И. Сатпаева»;
- Central-Asian rows get "name + city" variants (Almaty / Алматы / Алма-Ата …), so "универ алматы" works.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata

from rapidfuzz import fuzz, process

from ..config import settings
from ..models import Candidate
from .sources import wikidata

log = logging.getLogger("campuslens.resolve")

CENTRAL_ASIA = {"Q232", "Q813", "Q265", "Q863", "Q874"}
COUNTRY_NAMES = {
    "Q232": "Казахстан", "Q813": "Кыргызстан", "Q265": "Узбекистан", "Q863": "Таджикистан", "Q874": "Туркменистан",
    "Q159": "Россия", "Q43": "Турция", "Q30": "США", "Q145": "Великобритания", "Q183": "Германия", "Q16": "Канада",
    "Q148": "Китай", "Q884": "Южная Корея", "Q17": "Япония", "Q878": "ОАЭ", "Q213": "Чехия", "Q36": "Польша",
    "Q38": "Италия", "Q142": "Франция", "Q55": "Нидерланды", "Q408": "Австралия", "Q833": "Малайзия",
    "Q334": "Сингапур", "Q184": "Беларусь", "Q227": "Азербайджан", "Q230": "Грузия", "Q39": "Швейцария",
    "Q34": "Швеция", "Q31": "Бельгия", "Q40": "Австрия", "Q45": "Португалия", "Q29": "Испания", "Q79": "Египет",
    "Q668": "Индия", "Q219": "Болгария", "Q28": "Венгрия", "Q211": "Латвия", "Q37": "Литва", "Q191": "Эстония",
    "Q20": "Норвегия", "Q35": "Дания", "Q33": "Финляндия", "Q27": "Ирландия", "Q212": "Украина",
}
CITY_ALIASES = {
    "Алма-Ата": ["Алматы", "Almaty", "Алматы қаласы"], "Астана": ["Astana", "Нур-Султан", "Nur-Sultan", "Целиноград"],
    "Шымкент": ["Shymkent", "Чимкент"], "Караганда": ["Karaganda", "Karagandy", "Қарағанды"], "Актобе": ["Aktobe", "Ақтөбе"],
    "Тараз": ["Taraz", "Джамбул"], "Павлодар": ["Pavlodar"], "Усть-Каменогорск": ["Oskemen", "Ust-Kamenogorsk", "Өскемен"],
    "Семей": ["Semey", "Семипалатинск"], "Атырау": ["Atyrau"], "Костанай": ["Kostanay", "Қостанай"],
    "Кызылорда": ["Kyzylorda", "Қызылорда"], "Уральск": ["Oral", "Uralsk", "Орал"], "Петропавловск": ["Petropavl"],
    "Актау": ["Aktau", "Ақтау"], "Талдыкорган": ["Taldykorgan", "Талдықорған"], "Туркестан": ["Turkistan", "Түркістан"],
    "Кокшетау": ["Kokshetau", "Көкшетау"], "Бишкек": ["Bishkek"], "Ташкент": ["Tashkent", "Toshkent"], "Душанбе": ["Dushanbe"],
    "Ашхабад": ["Ashgabat"], "Москва": ["Moscow", "Moskva"], "Санкт-Петербург": ["Saint Petersburg", "Питер", "СПб"],
}
GENERIC = re.compile(
    r"\b(university|universitet|universiteti|univer|universität|université|universidad|università|universiteit|"
    r"универ|университет|университеті|университета|унив|ун-т|institute|институт|instituti|academy|академия|академиясы|"
    r"college|колледж|the|of|and|им|имени|атындағы|named|after|state|государственный|мемлекеттік|national|"
    r"национальный|ұлттық|technical|технический|техникалық)\b",
    re.I,
)
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("ё", "е")
    s = re.sub(r"[^\w\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_generic(s: str) -> str:
    out = GENERIC.sub(" ", s)
    return re.sub(r"\s+", " ", out).strip()


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(ch, ch) for ch in s)


def variants(name: str) -> set[str]:
    n = normalize(name)
    if not n:
        return set()
    out = {n}
    stripped = strip_generic(n)
    if stripped:
        out.add(stripped)
    if re.search(r"[а-яәғқңөұүһі]", n):
        t = translit(n)
        out.add(t)
        ts = strip_generic(t)
        if ts:
            out.add(ts)
    return out


class Index:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.choice_texts: list[str] = []      # cid -> normalized name variant
        self.choice_row: list[int] = []        # cid -> row index
        self.row_choices: list[list[int]] = [] # row index -> cids
        self.city_rows: dict[str, set[int]] = {}   # normalized city alias -> rows in that city
        self.city_aliases: list[str] = []

    def load(self, path=None) -> None:
        path = path or settings.data_dir / "universities.json"
        if not path.exists():
            log.warning("no offline index at %s", path)
            return
        self.rows = json.loads(path.read_text(encoding="utf-8"))
        self.by_id = {}
        self.choice_texts, self.choice_row, self.row_choices, self.city_rows = [], [], [], {}
        for i, r in enumerate(self.rows):
            self.by_id[r["id"]] = r
            names = [r.get("ru"), r.get("en"), r.get("kk")] + list(r.get("aliases") or [])
            texts: set[str] = set()
            for n in names:
                if n:
                    texts |= variants(n)
            if r.get("city"):
                for cv in {normalize(r["city"]), translit(normalize(r["city"]))} | {normalize(a) for a in CITY_ALIASES.get(r["city"], [])}:
                    if len(cv) >= 4:
                        self.city_rows.setdefault(cv, set()).add(i)
            if r["country"] in CENTRAL_ASIA and r.get("city"):
                city_vars = {normalize(r["city"]), translit(normalize(r["city"]))}
                city_vars |= {normalize(a) for a in CITY_ALIASES.get(r["city"], [])}
                bases = [strip_generic(normalize(x)) or normalize(x) for x in (r.get("ru"), r.get("en")) if x]
                for b in bases:
                    for cv in city_vars:
                        texts.add(f"{b} {cv}")
                        texts.add(f"{translit(b)} {cv}")
            # one-letter leftovers ("Q", "M") would partial-match anything
            cids: list[int] = []
            for t in texts:
                if len(t) < 3 or not re.search(r"\w{2}", t):
                    continue
                cids.append(len(self.choice_texts))
                self.choice_texts.append(t)
                self.choice_row.append(i)
            self.row_choices.append(cids)
        self.city_aliases = list(self.city_rows)
        log.info("index loaded: %d universities, %d name variants", len(self.rows), len(self.choice_texts))

    @staticmethod
    def _notability(r: dict) -> int:
        return (2 if r.get("commons") else 0) + (1 if r.get("site") else 0) + (1 if r.get("ru") else 0) + (1 if r.get("coord") else 0)

    def _city_in(self, qn: str) -> tuple[str | None, str]:
        """A city mentioned in the query (typos allowed): returns (alias, query without the city)."""
        toks = qn.split()
        grams = [t for t in toks] + [f"{a} {b}" for a, b in zip(toks, toks[1:])]
        best: tuple[str, float, str] | None = None
        for g in grams:
            if len(g) < 4 or GENERIC.fullmatch(g):
                continue
            m = process.extractOne(g, self.city_aliases, scorer=fuzz.ratio, score_cutoff=86)
            if m and (best is None or m[1] > best[1]):
                best = (g, m[1], m[0])
        if not best:
            return None, qn
        g, _, alias = best
        rest = re.sub(r"\s+", " ", qn.replace(g, " ", 1)).strip()
        return alias, rest

    def search(self, q: str, limit: int = 8) -> list[Candidate]:
        if not self.choice_texts:
            return []
        qn = normalize(q)
        if len(qn) < 2:
            return []
        qs = strip_generic(qn) or qn
        best: dict[int, float] = {}

        def consider(row: int, score: float, text: str) -> None:
            r = self.rows[row]
            bonus = 6 if qs == text else 0
            # the full (unstripped) label equals the query: strongest possible evidence
            if any(normalize(x) == qn for x in (r.get("ru"), r.get("en"), r.get("kk")) if x):
                bonus += 12
            if r["country"] in CENTRAL_ASIA:
                bonus += 3
            bonus += self._notability(r)
            best[row] = max(best.get(row, 0), score + bonus)

        for text, score, cid in process.extract(qs, self.choice_texts, scorer=fuzz.WRatio, limit=120, score_cutoff=74):
            consider(self.choice_row[cid], score, text)

        # "универ алматы", "Astana university", "Almatyy KBTU": the city narrows the search and boosts its rows
        alias, rest = self._city_in(qn)
        if alias:
            city_rows = self.city_rows[alias]
            rest_s = strip_generic(rest)
            if rest_s:
                sub = [cid for row in city_rows for cid in self.row_choices[row]]
                sub_texts = [self.choice_texts[c] for c in sub]
                for text, score, k in process.extract(rest_s, sub_texts, scorer=fuzz.WRatio, limit=40, score_cutoff=60):
                    consider(self.choice_row[sub[k]], score, text)
            else:  # only a city was named: its universities, most notable first
                for row in city_rows:
                    consider(row, 70 + 2 * self._notability(self.rows[row]), "")
            for row in list(best):
                if row in city_rows:
                    best[row] += 10

        ranked = sorted(best.items(), key=lambda x: -x[1])[:limit]
        out: list[Candidate] = []
        for row, score in ranked:
            r = self.rows[row]
            out.append(Candidate(
                qid=r["id"], label=r.get("ru") or r.get("en") or r.get("kk") or r["id"],
                city=r.get("city"), country=COUNTRY_NAMES.get(r["country"]),
                score=min(score, 110) / 100.0, origin="index",
            ))
        return out


index = Index()


async def resolve(q: str) -> list[Candidate]:
    """Offline index first; live Wikidata search is added unless the index already has a near-exact hit."""
    q = q.strip()
    if not q:
        return []
    local = index.search(q)
    remote: list[Candidate] = []
    qn = normalize(q)
    # a confident local hit (exact stripped/full-label match, or a city-narrowed list) answers instantly;
    # otherwise Wikidata gets a short, hard-capped chance so typing never stalls
    exact_local = bool(local) and local[0].score >= 1.0
    if not exact_local:
        try:
            remote = await asyncio.wait_for(wikidata.search(q), timeout=1.5)
        except Exception as e:  # noqa: BLE001
            log.warning("wikidata search failed: %s", e or type(e).__name__)
    merged: dict[str, Candidate] = {c.qid: c for c in local}
    for i, c in enumerate(remote):
        # Wikidata's top hit outranks a fuzzy index hit unless the index matched the full label exactly
        c.score = 1.02 if (i == 0 and not exact_local) else 0.55 + 0.45 * c.score
        if c.qid in merged:
            m = merged[c.qid]
            m.score = min(1.1, m.score + 0.3 * c.score)
            m.description = m.description or c.description
            m.logo_url = m.logo_url or c.logo_url
            m.city = m.city or c.city
            m.country = m.country or c.country
        else:
            merged[c.qid] = c
    out = list(merged.values())
    for c in out:
        if normalize(c.label) == qn:
            c.score = max(c.score, 1.05)
    out.sort(key=lambda c: -c.score)
    return out[:10]
