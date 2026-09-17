"""Run the globe → campus transition several times in a row and report which runs break.

    python scripts/repeat_transition.py

Each run: search → pick (click or Enter) → sample the camera, cloud overlay, markers and the photo scene →
go back to the planet → wait (sometimes only 0.4 s, to catch leftover timers) → next run.
"""
from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173/"
RUNS = [
    ("Назарбаев", "Назарбаев Университет", "click", 3.0),
    ("Назарбаев", "Назарбаев Университет", "click", 0.4),
    ("Казахский национальный университет", "Казахский национальный университет", "click", 3.0),
    ("Назарбаев", "Назарбаев Университет", "enter", 0.4),
    ("KAIST", "KAIST", "click", 0.4),
    ("Назарбаев", "Назарбаев Университет", "click", 3.0),
]

ARM = """() => {
  const m = window.__map; window.__s = []; const t0 = performance.now()
  const wrap = [...document.querySelectorAll('canvas')].map((c) => c.parentElement).find((p) => p && p.className.includes('z-30'))
  const sample = () => {
    const t = performance.now() - t0
    if (t > 10500) return
    const c = m.getCenter()
    let markers = null
    try { markers = m.getPaintProperty('unis-point', 'circle-opacity') } catch (e) {}
    const scene = [...document.querySelectorAll('div')].some((d) => d.textContent === 'Так выглядит кампус' && d.offsetParent && getComputedStyle(d.closest('.z-40') || d).opacity !== '0')
    window.__s.push({ t: Math.round(t), z: +m.getZoom().toFixed(2), lng: +c.lng.toFixed(1), clouds: wrap ? +(+wrap.style.opacity).toFixed(2) : -1, markers, scene })
    setTimeout(sample, 250)
  }
  sample()
}"""


def summarize(s: list[dict]) -> dict:
    zs = [x["z"] for x in s]
    first_zoom_up = next((x["t"] for x in s if x["z"] > s[0]["z"] + 0.15), None)
    lngs = [x["lng"] for x in s]
    first_turn = next((x["t"] for i, x in enumerate(s) if i and abs(x["lng"] - s[0]["lng"]) > 3), None)
    return {
        "maxZoom": max(zs), "zoomAt1s": next((x["z"] for x in s if x["t"] >= 1000), None),
        "firstTurnMs": first_turn, "firstZoomUpMs": first_zoom_up,
        "cloudsSeen": any(x["clouds"] > 0.2 for x in s),
        "markersDuringDive": sorted({str(x["markers"]) for x in s if 1000 < x["t"] < 6000}),
        "sceneAtMs": next((x["t"] for x in s if x["scene"]), None),
    }


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 1440, "height": 810}, locale="ru-RU")
        page.on("console", lambda m: print("   console:", m.type, m.text[:160]) if m.type == "error" else None)
        page.on("pageerror", lambda e: print("   PAGE ERROR:", str(e)[:200]))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_function("window.__map && window.__map.getLayer('unis-point')", timeout=90000)
        page.wait_for_timeout(3000)
        runs = RUNS[:int(__import__('os').environ.get('RUNS', len(RUNS)))]
        for i, (query, pick, how, pause) in enumerate(runs, 1):
            box = page.get_by_placeholder("Например").first
            box.click()
            box.fill("")
            box.type(query, delay=25)
            item = page.get_by_text(pick, exact=True).first
            item.wait_for(timeout=20000)
            page.evaluate(ARM)
            if how == "enter":
                box.press("Enter")
            else:
                item.click()
            page.wait_for_timeout(10800)
            s = page.evaluate("() => window.__s")
            print(f"run {i} [{pick} / {how} / pause {pause}s]:", json.dumps(summarize(s), ensure_ascii=False), flush=True)
            # back to the planet
            btn = page.get_by_role("button", name="3D-карта")
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(400)
            back = page.get_by_title("Назад к планете")
            if back.count():
                back.first.click()
            else:
                print("   no back button!")
            page.wait_for_timeout(int(pause * 1000))
        browser.close()


if __name__ == "__main__":
    main()
