"""Precision / recall of the verification pipeline against hand-labelled photos.

    python scripts/eval.py --list Q2783344      # print photo ids + titles + thumb paths to label
    python scripts/eval.py                      # evaluate data/labels.json against cached profiles

labels.json format:
{
  "Q2783344": { "<photo_id>": {"belongs": true, "category": "lab"}, ... },
  "Q427677":  { ... }
}
"belongs" — the photo really shows this university (or its city for category "city").
Positive prediction = photo kept in the profile (verified or likely); negative = rejected.
strict_precision also requires the right category (when the label has one);
featured_precision is measured on the curated set the profile shows first.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache  # noqa: E402
from app.config import settings  # noqa: E402

LABELS = settings.data_dir / "labels.json"
REPORT = settings.data_dir / "eval_report.json"


async def list_photos(qid: str) -> None:
    p = await cache.get_profile(qid)
    if not p:
        print("no cached profile; build it first (scripts/smoke.py or the UI)")
        return
    print(f"# {p.university.name} — {len(p.photos)} kept, {len(p.rejected)} rejected")
    for ph in p.photos + p.rejected:
        print(f'"{ph.id}": {{"belongs": true, "category": "{ph.category}"}},  # {"KEPT" if not ph.rejected else "REJ "} {ph.confidence:.2f} {ph.source} | {(ph.title or "")[:50]} | {settings.thumbs_dir / (ph.id + ".jpg")}')


async def evaluate() -> None:
    labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    labels = {k: v for k, v in labels.items() if not k.startswith("_")}
    if not labels:
        print("no labels; see --list")
        return
    tp = fp = fn = tn = 0
    cat_ok = cat_n = 0
    strict_ok = 0
    feat_ok = feat_n = 0
    per: dict[str, dict] = {}
    for qid, items in labels.items():
        p = await cache.get_profile(qid)
        if not p:
            print(f"{qid}: no cached profile, skipped")
            continue
        by_id = {ph.id: ph for ph in p.photos + p.rejected}
        row = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for pid, lab in items.items():
            ph = by_id.get(pid)
            if not ph:
                continue
            predicted = not ph.rejected
            truth = bool(lab.get("belongs"))
            key = "tp" if predicted and truth else "fp" if predicted and not truth else "fn" if truth else "tn"
            row[key] += 1
            if predicted and truth and lab.get("category"):
                cat_n += 1
                cat_ok += ph.category == lab["category"]
            if predicted and truth and (not lab.get("category") or ph.category == lab["category"]):
                strict_ok += 1
            if predicted and getattr(ph, "featured", False):
                feat_n += 1
                feat_ok += truth and (not lab.get("category") or ph.category == lab["category"])
        per[qid] = row
        tp += row["tp"]; fp += row["fp"]; fn += row["fn"]; tn += row["tn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    report = {"labelled": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
              "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
              "category_accuracy": round(cat_ok / cat_n, 3) if cat_n else None,
              "strict_precision": round(strict_ok / (tp + fp), 3) if tp + fp else None,
              "featured_precision": round(feat_ok / feat_n, 3) if feat_n else None, "featured": feat_n,
              "per_university": per}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


async def main() -> None:
    await cache.init()
    if len(sys.argv) > 2 and sys.argv[1] == "--list":
        await list_photos(sys.argv[2])
    else:
        await evaluate()


if __name__ == "__main__":
    asyncio.run(main())
