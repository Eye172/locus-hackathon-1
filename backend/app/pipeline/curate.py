"""Curator: a compact, well-sorted set instead of everything that passed.

Per category it features at most `curate_quota[cat]` photos, ranked by confidence and usefulness, skipping shots
that look too much like one already featured (CLIP cosine). Everything else stays in the profile, behind "show all".
"""
from __future__ import annotations

import numpy as np

from ..config import settings
from ..models import Photo

DIVERSITY_COSINE = 0.86


def rank(p: Photo) -> float:
    q = (p.quality / 3) if p.quality is not None else 0.5
    return 0.65 * p.confidence + 0.35 * q + (0.04 if p.level == "verified" else 0.0) - (0.12 if p.outdated else 0.0)


def feature(photos: list[Photo], embs: dict[str, np.ndarray]) -> list[Photo]:
    """Marks `featured` in place and returns photos ordered: featured by rank, then the rest by rank."""
    ordered = sorted(photos, key=rank, reverse=True)
    picked: dict[str, list[np.ndarray | None]] = {}
    for p in ordered:
        p.featured = False
        quota = settings.curate_quota.get(p.category, 6)
        chosen = picked.setdefault(p.category, [])
        if len(chosen) >= quota or (p.quality is not None and p.quality < 2 and p.level != "verified"):
            continue
        e = embs.get(p.id)
        if e is not None and any(c is not None and float(np.dot(e, c)) >= DIVERSITY_COSINE for c in chosen):
            continue
        p.featured = True
        chosen.append(e)
    return [p for p in ordered if p.featured] + [p for p in ordered if not p.featured]
