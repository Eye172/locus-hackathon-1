"""Higgsfield cutscene for a university (SPEC §10): a short cinematic clip from the best verified campus photo,
played between the fly-through and the profile. It is a stylised TRANSITION and is labelled as AI-generated in the UI;
it is never shown as a campus photo.

    python scripts/higgsfield_cutscene.py Q2783344                 # dry run: prints the request, spends nothing
    python scripts/higgsfield_cutscene.py Q2783344 --spend         # submits, polls, saves frontend/public/cutscenes/Q2783344.mp4

Endpoint: copy the model URL from console.higgsfield.ai → model → "Use in API" (e.g. an image-to-video Kling 3.0 URL)
and pass it with --endpoint or set HIGGSFIELD_ENDPOINT in backend/.env. Docs: https://docs.higgsfield.ai
Pricing shown in the console (Sep 2026): Kling 3.0 from 0.042/s, Wan 3.0 from 0.0476/s — a 6 s clip ≈ 0.3.
Failed / nsfw requests are not charged; output is kept ≥ 7 days, so the file is downloaded immediately.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import cache  # noqa: E402
from app.config import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "frontend" / "public" / "cutscenes"
PROMPT = ("Slow cinematic drone push-in towards the main building of {name} in {city}, golden hour, gentle camera "
          "movement, realistic architecture preserved exactly as in the photo, no people added, no text, no logos")


async def pick_photo(qid: str) -> tuple[dict, str]:
    await cache.init()
    p = await cache.get_profile(qid)
    if not p:
        sys.exit(f"{qid}: no cached profile — open it in the app first")
    for ph in sorted(p.photos, key=lambda x: -x.confidence):
        if ph.level == "verified" and ph.category in ("campus", "library", "dormitory") and ph.url.startswith("http"):
            return {"name": p.university.name, "city": p.university.city or ""}, ph.url
    sys.exit(f"{qid}: no verified campus photo with a public URL")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--endpoint", default=os.environ.get("HIGGSFIELD_ENDPOINT", ""))
    ap.add_argument("--duration", type=int, default=6)
    ap.add_argument("--spend", action="store_true", help="actually call the API (uses credits)")
    args = ap.parse_args()
    key = settings.higgsfield_api_key or os.environ.get("HIGGSFIELD_API_KEY")
    if not key or ":" not in key:
        sys.exit("HIGGSFIELD_API_KEY (id:secret) missing in backend/.env")
    meta, image_url = asyncio.run(pick_photo(args.qid))
    body = {"prompt": PROMPT.format(**meta), "input_images": [{"type": "image_url", "image_url": image_url}],
            "duration": args.duration, "aspect_ratio": "16:9"}
    print("endpoint:", args.endpoint or "(not set — pass --endpoint from the console's 'Use in API' tab)")
    print("request:", json.dumps(body, ensure_ascii=False, indent=1))
    if not args.spend:
        print("dry run: nothing sent, no credits used. Add --spend to generate.")
        return
    if not args.endpoint:
        sys.exit("--endpoint is required with --spend")
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    r = httpx.post(args.endpoint, json=body, headers=headers, timeout=60)
    r.raise_for_status()
    sub = r.json()
    print("submitted:", sub.get("request_id"), sub.get("status"))
    status_url = sub["status_url"]
    t0 = time.time()
    while True:
        s = httpx.get(status_url, headers=headers, timeout=30).json()
        st = s.get("status")
        if st in ("completed", "failed", "nsfw", "canceled"):
            break
        if time.time() - t0 > 600:
            sys.exit("timeout while polling; check the console, nothing else is charged")
        time.sleep(3)
    if st != "completed":
        sys.exit(f"generation ended as {st} (not charged)")
    video_url = (s.get("video") or {}).get("url")
    if not video_url:
        sys.exit(f"no video url in response: {json.dumps(s)[:300]}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = OUT_DIR / f"{args.qid}.raw.mp4"
    raw.write_bytes(httpx.get(video_url, timeout=120).content)
    final = OUT_DIR / f"{args.qid}.mp4"
    try:  # normalise for the player: 1600x900, no audio, faststart
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-an", "-vf", "scale=1600:900:force_original_aspect_ratio=increase,crop=1600:900",
                        "-c:v", "libx264", "-crf", "22", "-movflags", "+faststart", str(final)], check=True)
        raw.unlink()
    except Exception:  # noqa: BLE001
        raw.replace(final)
    print("saved", final, final.stat().st_size // 1024, "KB — the globe plays it before opening the profile")


if __name__ == "__main__":
    main()
