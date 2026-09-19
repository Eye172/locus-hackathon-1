"""Curator: a compact, well-sorted set instead of everything that passed.

Per category it features at most `curate_quota[cat]` photos, ranked by confidence and usefulness, skipping shots
that look too much like one already featured (CLIP cosine). Everything else stays in the profile, behind "show all".
"""
from __future__ import annotations

import numpy as np

from ..config import settings
from ..models import Photo

DIVERSITY_COSINE = 0.86


BRANDED = {"banner", "text", "logo"}


def rank(p: Photo) -> float:
    q = (p.quality / 3) if p.quality is not None else 0.5
    # a real photo with the university's banner across the bottom is still evidence, but between it and a clean
    # shot of the same place the clean one goes into the album
    branded = 0.08 if p.ai and BRANDED.intersection(p.ai.flags) else 0.0
    return (0.65 * p.confidence + 0.35 * q + (0.04 if p.level == "verified" else 0.0)
            - (0.12 if p.outdated else 0.0) - branded + looks(p))


def looks(p: Photo) -> float:
    """How good it is as a picture - the inspector's beauty, the cover editor's where it looked closer (a posed group
    goes back). Zero when neither said anything, so old verdicts keep their order."""
    b = p.look.beauty if p.look else p.ai.beauty if p.ai and p.ai.beauty is not None else None
    if b is None:
        return 0.0
    return 0.06 * (b - 1.5) - (0.08 if p.look and p.look.posed else 0.0)


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
