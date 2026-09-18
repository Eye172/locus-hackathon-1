"""Two-level de-duplication: perceptual hash for copies, CLIP cosine for visually similar shots."""
from __future__ import annotations

import imagehash
import numpy as np

from ..config import settings
from ..models import Photo, PhotoRef
from .fetch import Fetched
from .verify import SOURCE_PRIOR


def hamming(h1: str, h2: str) -> int:
    return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)


def _phash_int(h: str | None) -> int | None:
    try:
        return int(h, 16) if h else None
    except ValueError:
        return None


def merge_exact(items: list[Fetched]) -> list[Fetched]:
    """Group copies (Hamming <= threshold). The best-source, highest-resolution copy represents the group;
    other copies are attached as extra_sources so provenance is preserved."""
    # every photo is compared with every group: ~100k comparisons for a grown profile. imagehash builds two numpy
    # arrays per comparison (~3 s per profile update, holding the event loop); a XOR of the hashes as integers gives
    # the same bit count in a fraction of a microsecond
    limit = settings.phash_max_distance
    groups: list[list[Fetched]] = []
    heads: list[int | None] = []
    for f in items:
        h = _phash_int(f.phash)
        for g, gh in zip(groups, heads):
            same_bytes = bool(f.sha1) and f.sha1 == g[0].sha1
            if same_bytes or (h is not None and gh is not None and (h ^ gh).bit_count() <= limit):
                g.append(f)
                break
        else:
            groups.append([f])
            heads.append(h)
    out: list[Fetched] = []
    for g in groups:
        g.sort(key=lambda x: (SOURCE_PRIOR.get(x.cand.source, 0), x.width * x.height), reverse=True)
        rep = g[0]
        rep.extra_sources = [x.cand for x in g[1:]]
        # borrow metadata from copies when the representative lacks it
        for x in g[1:]:
            if rep.cand.date is None and x.cand.date:
                rep.cand.date, rep.cand.date_source = x.cand.date, x.cand.date_source
            if rep.cand.lat is None and x.cand.lat is not None:
                rep.cand.lat, rep.cand.lon = x.cand.lat, x.cand.lon
            if not rep.cand.author and x.cand.author:
                rep.cand.author = x.cand.author
            if not rep.cand.license and x.cand.license:
                rep.cand.license = x.cand.license
        out.append(rep)
    return out


def cluster_similar(photos: list[Photo], embs: dict[str, np.ndarray]) -> list[Photo]:
    """Hide near-duplicates (cosine >= threshold) behind the most confident photo of each cluster."""
    kept: list[Photo] = []
    order = sorted(photos, key=lambda p: (p.confidence, p.width * p.height), reverse=True)
    for p in order:
        e = embs.get(p.id)
        placed = False
        if e is not None:
            for k in kept:
                if k.category != p.category:
                    continue
                ke = embs.get(k.id)
                if ke is None:
                    continue
                sim = float(np.dot(e, ke))
                if sim >= settings.near_dup_cosine:
                    k.similar.append(PhotoRef(id=p.id, thumb=p.thumb, source=p.source, page_url=p.page_url,
                                              similarity=round(sim, 3)))
                    placed = True
                    break
        if not placed:
            kept.append(p)
    return kept
