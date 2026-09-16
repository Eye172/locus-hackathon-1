"""Build an offline fuzzy-search index of universities from Wikidata SPARQL.

Run once (or occasionally) to refresh backend/data/universities.json.
The live pipeline still resolves unknown names through the Wikidata search API;
this index gives typo-tolerant matching and instant results for known universities.
"""
import json, sys, time, pathlib, re
import httpx

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "universities.json"
UA = {"User-Agent": "CampusLens/0.1 (LOCUS hackathon; contact: nnurkhan91@gmail.com)",
      "Accept": "application/sparql-results+json"}

# (country QID, use subclass closure?)  Central Asia first, then neighbours and popular destinations.
COUNTRIES = [
    ("Q232", True),  # Kazakhstan
    ("Q813", True),  # Kyrgyzstan
    ("Q265", True),  # Uzbekistan
    ("Q863", True),  # Tajikistan
    ("Q874", True),  # Turkmenistan
    ("Q159", False), # Russia
    ("Q43", False),  # Turkey
    ("Q30", False),  # USA
    ("Q145", False), # UK
    ("Q183", False), # Germany
    ("Q16", False),  # Canada
    ("Q148", False), # China
    ("Q884", False), # South Korea
    ("Q17", False),  # Japan
    ("Q878", False), # UAE
    ("Q213", False), # Czechia
    ("Q36", False),  # Poland
    ("Q38", False),  # Italy
    ("Q142", False), # France
    ("Q55", False),  # Netherlands
    ("Q408", False), # Australia
    ("Q833", False), # Malaysia
    ("Q334", False), # Singapore
    ("Q184", False), # Belarus
    ("Q227", False), # Azerbaijan
    ("Q230", False), # Georgia
    ("Q39", False),  # Switzerland
    ("Q34", False),  # Sweden
    ("Q31", False),  # Belgium
    ("Q40", False),  # Austria
    ("Q45", False),  # Portugal
    ("Q29", False),  # Spain
    ("Q79", False),  # Egypt
    ("Q668", False), # India
    ("Q219", False), # Bulgaria
    ("Q28", False),  # Hungary
    ("Q211", False), # Latvia
    ("Q37", False),  # Lithuania
    ("Q191", False), # Estonia
    ("Q20", False),  # Norway
    ("Q35", False),  # Denmark
    ("Q33", False),  # Finland
    ("Q27", False),  # Ireland
    ("Q212", False), # Ukraine
]

# Classes are the full P279* closure of "university" (Q3918) that actually has instances, fetched once at start
# (269 classes, ~150 with instances). Querying "P31 ?cls VALUES ?cls {...}" in chunks is equivalent to the closure
# query but never times out on big countries. Q38723 (higher education institution) is added for institutes/academies.
CHUNK = 40
EXTRA_CLASSES = ["Q38723"]


def fetch_classes(c):
    q = """SELECT ?cls (COUNT(?x) AS ?n) WHERE { ?cls wdt:P279* wd:Q3918 . ?x wdt:P31 ?cls } GROUP BY ?cls HAVING (?n > 0)"""
    r = c.get("https://query.wikidata.org/sparql", params={"query": q})
    r.raise_for_status()
    cls = [b["cls"]["value"].rsplit("/", 1)[-1] for b in r.json()["results"]["bindings"]]
    return sorted(set(cls) | set(EXTRA_CLASSES))


def query(country, closure, classes=None):
    cls = ("wdt:P31/wdt:P279* wd:Q3918" if closure
           else "wdt:P31 ?cls . VALUES ?cls { " + " ".join(f"wd:{c}" for c in classes) + " }")
    return f"""
SELECT ?item ?en ?ru ?kk ?coord ?site ?commons ?cityLabel
       (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?aliases) WHERE {{
  ?item {cls} . ?item wdt:P17 wd:{country} .
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en)="en") }}
  OPTIONAL {{ ?item rdfs:label ?ru FILTER(LANG(?ru)="ru") }}
  OPTIONAL {{ ?item rdfs:label ?kk FILTER(LANG(?kk)="kk") }}
  OPTIONAL {{ ?item wdt:P625 ?coord }}
  OPTIONAL {{ ?item wdt:P856 ?site }}
  OPTIONAL {{ ?item wdt:P373 ?commons }}
  OPTIONAL {{ ?item wdt:P131 ?city . ?city rdfs:label ?cityLabel FILTER(LANG(?cityLabel)="ru") }}
  OPTIONAL {{ ?item skos:altLabel ?alt FILTER(LANG(?alt) IN ("ru","en","kk")) }}
}} GROUP BY ?item ?en ?ru ?kk ?coord ?site ?commons ?cityLabel
"""

def parse_coord(s):
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", s or "")
    return [float(m.group(2)), float(m.group(1))] if m else None

def main():
    # `python build_university_index.py Q833 Q334` refreshes only those countries and merges into the existing file
    only = set(a for a in sys.argv[1:] if a.startswith("Q"))
    rows = {}
    if only and OUT.exists():
        rows = {r["id"]: r for r in json.loads(OUT.read_text(encoding="utf-8")) if r["country"] not in only}
        for r in rows.values():
            r["aliases"] = set(r.get("aliases") or [])
    with httpx.Client(timeout=60, headers=UA) as c:
        classes = fetch_classes(c)
        chunks = [classes[i:i + CHUNK] for i in range(0, len(classes), CHUNK)]
        print(f"{len(classes)} university classes -> {len(chunks)} chunks", file=sys.stderr, flush=True)
        for country, closure in COUNTRIES:
            if only and country not in only:
                continue
            data = []
            for classes in ([None] if closure else chunks):   # closure countries: one query; others: class chunks
                for attempt in range(3):
                    try:
                        r = c.get("https://query.wikidata.org/sparql", params={"query": query(country, closure, classes)})
                        r.raise_for_status()
                        data += r.json()["results"]["bindings"]
                        break
                    except Exception as e:
                        print(f"{country}: attempt {attempt+1} failed: {str(e)[:80]}", file=sys.stderr, flush=True)
                        time.sleep(3)
                time.sleep(0.5)
            for b in data:
                qid = b["item"]["value"].rsplit("/", 1)[-1]
                row = rows.setdefault(qid, {"id": qid, "country": country, "aliases": set()})
                for k in ("en", "ru", "kk", "site", "commons"):
                    if k in b and not row.get(k): row[k] = b[k]["value"]
                if "cityLabel" in b and not row.get("city"): row["city"] = b["cityLabel"]["value"]
                if "coord" in b and not row.get("coord"): row["coord"] = parse_coord(b["coord"]["value"])
                if b.get("aliases", {}).get("value"):
                    row["aliases"].update(a for a in b["aliases"]["value"].split("|") if a)
            print(f"{country}: {len(data)} rows, total {len(rows)}", file=sys.stderr, flush=True)
            time.sleep(1.0)
    out = []
    for r in rows.values():
        r["aliases"] = sorted(r["aliases"])[:20]
        if r.get("en") or r.get("ru"): out.append(r)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} universities to {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
