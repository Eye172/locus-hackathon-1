"""Record the globe → campus transition in headless Chromium and write a contact sheet of frames.

    python scripts/record_transition.py                     # Назарбаев Университет
    python scripts/record_transition.py --query "KAIST" --pick KAIST

Output: docs/video/transition.webm and docs/video/transition_sheet.png (a frame every 0.5 s after the click).
Used to check the choreography frame by frame (spin + zoom together, clouds, white-out, photo).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "video"
BASE = "http://localhost:5173"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="Назарбаев")
    ap.add_argument("--pick", default="Назарбаев Университет")
    ap.add_argument("--seconds", type=float, default=9.0)
    args = ap.parse_args()
    tmp = OUT / "_rec"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(tmp),
                                  record_video_size={"width": 1280, "height": 720}, locale="ru-RU")
        page = ctx.new_page()
        page.set_default_timeout(60000)
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_timeout(4000)  # globe tiles + idle tile prefetch
        box = page.get_by_placeholder("Поиск университета").first
        box.click()
        box.type(args.query, delay=40)
        page.get_by_text(args.pick, exact=True).first.wait_for()
        t_ready = time.monotonic()
        page.get_by_text(args.pick, exact=True).first.click()
        t_click = time.monotonic()
        page.wait_for_timeout(int(args.seconds * 1000))
        video = page.video
        ctx.close()
        browser.close()
        src = Path(video.path())
    # the video starts at page creation: find the click offset from wall-clock times recorded around it
    started = src.stat().st_ctime
    final = OUT / "transition.webm"
    shutil.move(str(src), final)
    shutil.rmtree(tmp, ignore_errors=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(final)],
                               capture_output=True, text=True).stdout.strip() or 0)
    click_at = max(0.0, dur - args.seconds - (time.monotonic() - t_click - args.seconds) * 0)  # click happened `seconds` before the end
    click_at = max(0.0, dur - args.seconds)
    sheet = OUT / "transition_sheet.png"
    sheet.unlink(missing_ok=True)  # never show a stale sheet when the labelled variant fails
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{click_at:.2f}", "-i", str(final),
                    "-vf", "fps=4,scale=320:-1,drawtext=text='%{pts\\:hms}':x=6:y=6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.5,tile=6x4",
                    "-frames:v", "1", str(sheet)], check=False)
    if not sheet.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{click_at:.2f}", "-i", str(final),
                        "-vf", "fps=4,scale=320:-1,tile=6x4", "-frames:v", "1", str(sheet)], check=True)
    print(f"video {final} ({dur:.1f}s), click at {click_at:.2f}s, sheet {sheet}; ready→click {t_click - t_ready:.2f}s")


if __name__ == "__main__":
    main()
