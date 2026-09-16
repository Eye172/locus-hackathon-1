"""CLIP ViT-B/32: image embeddings, zero-shot category and junk classification."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import numpy as np

from ..config import settings
from ..models import CATEGORIES

log = logging.getLogger("campuslens.vision")

CATEGORY_PROMPTS: dict[str, list[str]] = {
    "campus": ["a university campus building exterior", "the main building of a university",
               "university campus grounds with walkways and trees", "a modern university building facade"],
    "dormitory": ["a student dormitory building", "a dorm room with beds and desks", "a student residence hall",
                  "a shared student bedroom"],
    "classroom": ["a lecture hall with rows of seats", "a classroom with desks and a whiteboard",
                  "students sitting in an auditorium during a lecture"],
    "library": ["a library reading room with bookshelves", "students studying in a library",
                "long bookshelves in a university library"],
    "lab": ["a science laboratory with equipment", "students working in a computer lab",
            "a research laboratory with instruments and glassware"],
    "sports": ["a sports stadium", "a gym or fitness hall", "an indoor swimming pool",
               "students playing sports on a field"],
    "student_life": ["students at a campus event", "a group of students celebrating together",
                     "a student festival or concert on campus", "students eating in a cafeteria"],
    "city": ["a city street with buildings and cars", "a city skyline", "a city park or landmark",
             "mountains and a city view"],
}
JUNK_PROMPTS: dict[str, str] = {
    "logo": "a logo or emblem on a plain background",
    "map": "a map",
    "screenshot": "a screenshot of a website or app",
    "document": "a scanned document with text",
    "poster": "a poster or banner with large text",
    "portrait": "a close-up portrait photo of one person",
    "diagram": "a diagram, chart or infographic",
    "certificate": "a certificate or award document",
}
JUNK_LABELS_RU = {
    "logo": "логотип", "map": "карта", "screenshot": "скриншот", "document": "документ", "poster": "постер с текстом",
    "portrait": "портрет", "diagram": "схема", "certificate": "сертификат",
}


class Clip:
    def __init__(self) -> None:
        self.model = None
        self.preprocess = None
        self.tokenizer = None
        self.text_emb: np.ndarray | None = None   # (n_cat + n_junk, 512)
        self.labels: list[str] = []
        self.n_cat = len(CATEGORIES)
        self._lock = threading.Lock()
        self.ready = False
        self.load_ms: int | None = None

    def load(self) -> None:
        with self._lock:
            if self.ready:
                return
            t = time.time()
            import torch
            import open_clip
            torch.set_num_threads(max(2, (torch.get_num_threads() or 4)))
            model, _, preprocess = open_clip.create_model_and_transforms(
                settings.clip_model, pretrained=settings.clip_pretrained)
            model.eval()
            tok = open_clip.get_tokenizer(settings.clip_model)
            prompts: list[str] = []
            owners: list[str] = []
            for c in CATEGORIES:
                for p in CATEGORY_PROMPTS[c]:
                    prompts.append(f"a photo of {p}")
                    owners.append(c)
            for k, p in JUNK_PROMPTS.items():
                prompts.append(p)
                owners.append(f"junk:{k}")
            with torch.no_grad():
                te = model.encode_text(tok(prompts)).float()
                te = te / te.norm(dim=-1, keepdim=True)
            te = te.cpu().numpy()
            labels = CATEGORIES + [f"junk:{k}" for k in JUNK_PROMPTS]
            agg = np.zeros((len(labels), te.shape[1]), dtype=np.float32)
            for i, lab in enumerate(labels):
                rows = [j for j, o in enumerate(owners) if o == lab]
                v = te[rows].mean(axis=0)
                agg[i] = v / np.linalg.norm(v)
            self.model, self.preprocess, self.tokenizer = model, preprocess, tok
            self.text_emb, self.labels = agg, labels
            self.ready = True
            self.load_ms = int((time.time() - t) * 1000)
            log.info("CLIP ready in %d ms", self.load_ms)

    def encode(self, images: list) -> np.ndarray:
        import torch
        self.load()
        if not images:
            return np.zeros((0, self.text_emb.shape[1]), dtype=np.float32)
        out = []
        with torch.no_grad():
            for i in range(0, len(images), settings.clip_batch):
                batch = torch.stack([self.preprocess(im) for im in images[i:i + settings.clip_batch]])
                f = self.model.encode_image(batch).float()
                f = f / f.norm(dim=-1, keepdim=True)
                out.append(f.cpu().numpy())
        return np.concatenate(out, axis=0)

    def classify(self, emb: np.ndarray) -> list[dict]:
        """Per image: category probabilities (renormalised), junk probabilities, junk total."""
        logits = 100.0 * emb @ self.text_emb.T
        logits = logits - logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p = p / p.sum(axis=1, keepdims=True)
        res = []
        for row in p:
            cats = {c: float(row[i]) for i, c in enumerate(CATEGORIES)}
            junk = {lab.split(":", 1)[1]: float(row[i]) for i, lab in enumerate(self.labels) if lab.startswith("junk:")}
            cat_total = sum(cats.values()) or 1e-6
            res.append({
                "categories": {c: v / cat_total for c, v in cats.items()},
                "categories_raw": cats,
                "junk": junk,
                "junk_total": float(sum(junk.values())),
                "junk_top": max(junk, key=junk.get),
            })
        return res


clip = Clip()


async def warmup() -> None:
    await asyncio.to_thread(clip.load)


async def encode(images: list) -> np.ndarray:
    return await asyncio.to_thread(clip.encode, images)
