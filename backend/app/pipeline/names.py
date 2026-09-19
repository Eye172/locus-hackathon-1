"""University names for the interface: Russian for a university in Russia, English for every other one.

Wikidata's "en" label is often the school's own-language name (Hochschule Mittweida, Universidad del Este,
École libre des hautes études), and a place found on a map carries its local name (Institut Européen
d'Administration des Affaires). An English-looking alternative - the English Wikipedia title, an English alias - wins
over such a label; a name with none is translated once by Gemini and remembered in kv 'name_en'.
The pipeline keeps using `University.names` as they are (hashtags and captions are written in the local name);
only `University.name` / `University.name_en` and the index's `name_en` are for display.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from .. import cache
from ..config import settings

log = logging.getLogger(__name__)

# the university word in another language: any of these makes a name not English
FOREIGN = re.compile(
    r"hochschul|universit[äa]t\b|universitaet|universiteit|hogeschool|högskol|høgskol|høyskol|yliopisto|korkeakoul|"
    r"egyetem|főiskola|uniwersytet|politechni|univerzit|univerza|sveučilišt|universidad|universidade|universit[àa]\b|"
    r"universit[ée]\b|universitatea|universitas|universiti\b|universitet|üniversite|universitesi|enstitü|"
    r"\binstitut\b|istituto|instituto|escuela|escola|scuola|[ée]cole\b|politecnic|politécnic|politehnic|polytechnique|"
    r"faculdade|facultad|faculté|akademie|akademia|akademija|accademia|academie\b|szkoła|wyższa|vysok[áé]|ülikool|"
    r"augstskola|sekolah|politeknik|technische|pädagogisch|supérieur|superieur|nationale\b|\bnacional\b|estadual|"
    r"autónoma|\bcatólica|pontifíci|pontificia|\bstudi\b|fachschule|kunstakademie",
    re.I)
# lowercase particles of other languages ("Universidade do Porto", "Hochschule für Musik"); capitalised ones are
# English place names (Des Moines, Del Mar)
PARTICLES = re.compile(r"(?<!\S)(?:für|und|der|des|del|della|degli|delle|di|du|y|et|voor|och|og|do|dos|zu|im)(?!\S)")
# a letter outside the Latin alphabets: Cyrillic, CJK, Arabic, Greek…
NON_LATIN = re.compile(r"[^\x00-\u024f\u02b0-\u02ff\u1e00-\u1eff\u2000-\u206f\u20a0-\u20cf]")
EN_WORD = re.compile(r"\b(?:University|College|Institute|School|Academy|Conservatory|Conservatoire|Polytechnic|"
                     r"Seminary|Faculty|Campus)\b")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# letters of Kazakh, Ukrainian, Belarusian, Kyrgyz, Serbian…: Cyrillic, but not Russian
NOT_RUSSIAN = re.compile(r"[ӘәҒғҚқҢңӨөҰұҮүҺһІіЇїЄєҐґЎўЈјЉљЊњЋћЏџЂђ]")


# romanised school words (Tōkyō Senmon Gakkō, Beijing Daxue): foreign unless an English word stands next to them,
# as in the official "Kanto Gakuin University"
ROMANISED = re.compile(r"daigak|gakk[oō]|gakuin|gakuen|senmon|daxue|xueyuan|daehak|đại học|trường|pamantasan|"
                       r"kolehiyo|mahavidyalaya|vidyalaya|vishwavidyalaya|jamia|madrasa", re.I)


def is_english(s: str | None) -> bool:
    return bool(s) and not NON_LATIN.search(s) and not FOREIGN.search(s) and not PARTICLES.search(s) \
        and not (ROMANISED.search(s) and not EN_WORD.search(s))


def is_russian(s: str | None) -> bool:
    return bool(s) and bool(CYRILLIC.search(s)) and not NOT_RUSSIAN.search(s)


def english_alternative(label: str | None, alternatives=()) -> str | None:
    """The label itself when it is English, else the first English alternative that names a university."""
    if is_english(label):
        return label
    for a in alternatives:
        if a and a != label and is_english(a) and EN_WORD.search(a):
            return a
    return None


# only a university in Russia is shown by its Russian name (Московский государственный университет); every other one
# by its English name, even when Wikidata has a Russian one (Stanford University, Nazarbayev University - the user's rule)
RU_COUNTRIES = {"Q159"}
# place names on a campus map stay Russian where Russian is an official language (Russia, Kazakhstan, Kyrgyzstan,
# Belarus: the streets of Astana are named in Russian too); elsewhere they are English
RU_PLACE_COUNTRIES = {"Q159", "Q232", "Q813", "Q184"}


def display(ru: str | None, en: str | None, fallback: str | None = None, country: str | None = None) -> str | None:
    """What the Russian interface shows: the Russian name of a university in Russia (`country` is
    its Wikidata id), the English name of any other. `ru` is Wikidata's "ru" label, so Kazakh letters in a person's
    name do not disqualify it; it only has to be Cyrillic ("SDU University", "KAIST" also sit in "ru" labels)."""
    if country in RU_COUNTRIES and ru and CYRILLIC.search(ru):
        return ru
    return en or fallback


def place_lang(lang: str, country: str | None) -> str:
    """The language of place names on a campus map: English outside the Russian-speaking countries (Lathrop Library,
    not «Библиотека Латроп»); the frontend's placeLang() is the same rule."""
    return lang if lang == "en" or not country or country in RU_PLACE_COUNTRIES else "en"


def web_display(name: str, name_en: str | None, country: str | None = None) -> str:
    """A university found on a map: its name there is in any language, so it counts as Russian only by its letters,
    and only in Russia (`country` is the map's country name; unknown counts as Russia)."""
    from .resolve import country_in, normalize
    cq = country_in(normalize(country)) if country else None
    return name if is_russian(name) and (cq is None or cq in RU_COUNTRIES) else (name_en or name)


UNI_WORD_RU = re.compile(r"университет|институт|школ|колледж|академи|консерватори|политехни|семинари|училищ", re.I)

# for the text writers: a name shown in English stays English in Russian prose
PROSE_RULE = ("Весь текст пиши по-русски, кириллицей. Название университета вставляй ровно так, как в поле name, "
              "даже если оно латиницей: не переводи, не склоняй и не ставь перед ним слово «университет» "
              "(«Stanford University основан…», «в Stanford University», а не «в Стэнфордском университете» и не "
              "«Университет Stanford University»).")


def swap_ru(text: str, name: str, ru: str | None) -> str:
    """Russian prose that still carries the Russian name (a model quoting Wikipedia: «Назарбаев Университет открыт…»,
    «Кыргызский национальный университет имени Жусупа Баласагына является…») gets the displayed name there, the
    «имени …» tail included, so nothing is said twice."""
    if not ru or CYRILLIC.search(name):
        return text
    plain = text.replace("\u0301", "")
    if ru not in plain:
        return text
    word = r"(?:аль-)?[А-ЯЁ][\w.\-]*"
    tail = rf"(?:\s+(?:имени|им\.)\s+{word}(?:\s+{word}){{0,2}})?"
    out = re.sub(re.escape(ru) + tail, lambda _m: name, plain)
    for dup in (f"{name} или {name}", f"{name} ({name})", f"{name}, {name}"):
        out = out.replace(dup, name)
    return out


def mostly_russian(text: str, name: str = "") -> bool:
    """Russian prose: most letters outside the university's name are Cyrillic (not English, not a transliteration)."""
    rest = text.replace(name, " ") if name else text
    letters = [c for c in rest if c.isalpha()]
    return not letters or sum(1 for c in letters if CYRILLIC.match(c)) >= 0.6 * len(letters)


def lead_name(sentence: str, name: str, ru: str | None = None) -> str:
    """A Wikipedia first sentence opens with the Russian name ("Стэ́нфордский университе́т, также … — частный
    университет…", "Мюнхенский технический университет, основанный…"): a university shown by its English name opens
    with that name instead."""
    if CYRILLIC.search(name):
        return sentence
    m = DASH.search(sentence)  # Wikipedia puts a no-break space before the dash
    if m:
        plain = sentence[:m.start()].replace("\u0301", "")
        if CYRILLIC.search(plain) and UNI_WORD_RU.search(plain) and len(plain.split()) <= 14:
            return f"{name} — {sentence[m.end():]}"
    plain = sentence.replace("\u0301", "")
    if ru and plain.lower().startswith(ru.lower()):  # no dash: the Russian name itself opens the sentence
        return name + plain[len(ru):]
    return sentence


DASH = re.compile(r"\s[—–]\s")
ABBR = re.compile(r"[(«]?(?:им|яп|англ|кит|каз|лат|нем|фр|исп|ит|кор|сокр|букв|г|гг|т|д|р|с|ул|пр|св|др|см|ок|[A-ZА-ЯЁ])",
                  re.I)


def first_sentence(text: str) -> str:
    """The lead sentence of an article: it ends at the first full stop after the defining dash, not at "им." or
    "(яп." inside the name before it."""
    m = DASH.search(text)
    start = m.end() if m else 0
    for e in re.finditer(r"\.(?=\s|$)", text):
        if e.start() < start:
            continue
        word = text[:e.start()].rsplit(None, 1)[-1] if text[:e.start()].strip() else ""
        if ABBR.fullmatch(word):
            continue
        return text[:e.start() + 1].strip()
    return text.strip()


def row_en(r: dict) -> str | None:
    """English name of an index row: `name_en` is written by scripts/english_names.py where the label is not English."""
    return r.get("name_en") or r.get("en") or None


def row_name(r: dict) -> str:
    return display(r.get("ru"), row_en(r), r.get("ru") or r.get("kk"), r.get("country")) or r["id"]


PROMPT = """Give the English name of every university below: its official English name when it has one, otherwise \
the usual English translation (Universidad del Este -> University of the East, Hochschule Mittweida -> Mittweida \
University of Applied Sciences, Technische Hochschule Lübeck -> Technical University of Applied Sciences Lübeck, \
École libre des hautes études -> Free School of Advanced Studies). Never answer with the name in its own language: \
the words for university, school, institute, academy and the like are always translated. Keep abbreviations and \
names of people and places, in Latin letters. Answer with a JSON array of strings, one per line of the list, in the \
same order.

{items}"""


async def translate(items: list[tuple[str, str | None]], timeout: float = 30.0) -> list[str | None]:
    """[(name, country)] -> English names, None where the model gave nothing usable."""
    if not items or settings.active_llm() != "gemini":
        return [None] * len(items)
    from .ai_inspector import gemini_post
    listing = "\n".join(f"{i + 1}. {n}" + (f" ({c})" if c else "") for i, (n, c) in enumerate(items))
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(items=listing)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0,
                                 "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}}}}
    j, _ = await gemini_post(body, timeout)
    out = json.loads(j["candidates"][0]["content"]["parts"][0]["text"])
    if not isinstance(out, list) or len(out) != len(items):
        raise ValueError(f"expected {len(items)} names, got {len(out) if isinstance(out, list) else type(out)}")
    return [x.strip() if isinstance(x, str) and x.strip() and not NON_LATIN.search(x) else None for x in out]


async def english(name: str, alternatives=(), country: str | None = None, timeout: float = 6.0) -> str:
    """English name for display; the name itself when nothing better is found in time."""
    alt = english_alternative(name, alternatives)
    if alt:
        return alt
    hit = await cache.kv_get("name_en", name)
    if hit:
        return hit.get("en") or name
    try:
        en = (await asyncio.wait_for(translate([(name, country)], timeout), timeout))[0]
    except Exception as e:  # noqa: BLE001  (no quota, a slow model: the own name is still a name)
        log.warning("name translation failed for %r: %r", name, e)
        return name
    if en:
        await cache.kv_set("name_en", name, {"en": en})
    return en or name
