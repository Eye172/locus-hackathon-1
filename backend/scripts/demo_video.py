"""Record a silent demo walkthrough of the running app (frontend on :5173, backend on :8000) with Playwright.

    python scripts/demo_video.py                # -> docs/video/demo.webm (+ demo.mp4 when ffmpeg is installed)
    python scripts/demo_video.py --qid Q2783344 --other Q427677

Captions are injected as a fixed overlay in the product's typography; add a voice-over later in any editor.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "video"
BASE = "http://localhost:5173"

CAPTION_JS = """
(text) => {
  let el = document.getElementById('cl-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'cl-caption';
    el.style.cssText = 'position:fixed;left:50%;bottom:36px;transform:translateX(-50%);max-width:70vw;padding:14px 22px;'
      + 'background:rgba(10,10,10,.82);color:#fff;font:600 22px/1.3 Manrope,Inter,system-ui,sans-serif;border-radius:14px;'
      + 'z-index:99999;letter-spacing:-.01em;box-shadow:0 8px 30px rgba(0,0,0,.35);transition:opacity .35s;pointer-events:none';
    document.body.appendChild(el);
  }
  el.style.opacity = text ? '1' : '0'; if (text) el.textContent = text;
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qid", default="Q2783344")
    ap.add_argument("--other", default="Q427677")
    ap.add_argument("--query", default="Nazarbaev Univercity")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.webm"):
        f.unlink()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1,
                                  record_video_dir=str(OUT), record_video_size={"width": 1600, "height": 900}, locale="ru-RU")
        page = ctx.new_page()
        page.set_default_timeout(90000)  # CLIP prewarms may share the CPU while we record

        def cap(text: str, hold: float) -> None:
            page.evaluate(CAPTION_JS, text)
            time.sleep(hold)

        # 1. globe
        page.goto(BASE + "/", wait_until="networkidle")
        cap("14 467 вузов на живой планете. Введите название — даже с опечаткой.", 6)
        # 2. search with a typo
        box = page.get_by_placeholder("Поиск университета").first
        box.click()
        for ch in args.query:
            box.type(ch, delay=70)
        page.wait_for_timeout(1500)
        cap("«Univercity» — опечатка, но кандидаты найдены.", 2.5)
        page.keyboard.press("Enter")
        cap("Перелёт сквозь облака: здания OpenStreetMap поднимаются из спутникового снимка.", 7)
        page.get_by_role("button", name=re.compile("Открыть профиль")).first.click()
        page.wait_for_timeout(2500)
        # 3. profile
        cap("Профиль: 8 категорий кейса, у каждого фото источник, дата и показатель достоверности.", 4)
        for _ in range(4):
            page.mouse.wheel(0, 380)
            page.wait_for_timeout(900)
        page.mouse.wheel(0, -1600)
        page.wait_for_timeout(600)
        thumb = page.locator("img[src*='/api/thumb/']").first
        thumb.wait_for(state="visible")
        thumb.scroll_into_view_if_needed()
        thumb.click()
        cap("Паспорт фото: сигналы объясняют, почему мы уверены. Ссылка ведёт на источник.", 5)
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        tabs = [("Отклонено", "Отклонённые кандидаты видны с причинами: логотипы, карты, документы, чужие здания.", 4),
                ("Брошюра vs реальность", "Брошюра vs реальность: официальные фото против снимков людей.", 4),
                ("Карта кампуса", "3D-кампус: полигон OSM, корпуса по типу, маршрут до центра, что рядом.", 7),
                ("Климат", "Климат за год в одном кольце, сезоны, индекс комфорта студента, роза ветров.", 6),
                ("Город", "Город: расстояния, транспорт, аэропорт, бюджет студента с источниками.", 5),
                ("Обход", "Обход кампуса и 3D-фото по картам глубины — без дорисовки.", 9),
                ("Режим жюри", "Режим жюри: тайминги источников, лог и измеренная точность 0,97.", 4)]
        for name, text, hold in tabs:
            page.get_by_role("button", name=re.compile("^" + re.escape(name))).first.click()  # tab names carry counts
            page.wait_for_timeout(800)
            cap(text, hold)
        # 4. compare
        page.goto(f"{BASE}/compare?a={args.qid}&b={args.other}", wait_until="networkidle")
        cap("Сравнение двух вузов: факты рядом, плюсы и минусы, чат отвечает только по данным профилей.", 7)
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(2500)
        # 5. outro
        page.goto(BASE + "/", wait_until="networkidle")
        cap("CampusLense — честный визуальный профиль университета. LOCUS 2026, кейс 1.", 5)
        page.evaluate(CAPTION_JS, "")
        page.wait_for_timeout(800)
        ctx.close()
        browser.close()

    webm = next(OUT.glob("*.webm"))
    final = OUT / "demo.webm"
    webm.replace(final)
    print("wrote", final, final.stat().st_size // 1024, "KB")
    if shutil.which("ffmpeg"):
        mp4 = OUT / "demo.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(final), "-c:v", "libx264", "-preset", "medium", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
        print("wrote", mp4, mp4.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
