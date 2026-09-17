"""End-to-end check of the globe levels in Chromium on the GPU: planet → (double click) Kazakhstan → (double click)
Almaty → click a university → wheel limits → level up; frame times per level and a screenshot of each step.

    python scripts/check_levels.py            # screenshots in docs/video/levels_*.png
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "video"
BASE = "http://localhost:5173/"

STATE = """() => { const m = window.__map; return { zoom: +m.getZoom().toFixed(2), min: +m.getMinZoom().toFixed(2), max: +m.getMaxZoom().toFixed(2),
  pitch: m.getMaxPitch(), crumbs: [...document.querySelectorAll('nav .crumb')].map((b) => b.textContent.trim()),
  visible: m.getLayersOrder().filter((id) => m.getLayoutProperty(id, 'visibility') !== 'none').length,
  popup: document.querySelector('.cl-popup')?.textContent?.slice(0, 80) ?? null } }"""
FRAMES_ON = """() => { window.__f = []; let last = performance.now(); window.__fr = true;
  const tick = (t) => { window.__f.push(t - last); last = t; if (window.__fr) requestAnimationFrame(tick) }; requestAnimationFrame(tick) }"""
FRAMES_OFF = """() => { window.__fr = false; const f = window.__f.slice(2).sort((a, b) => a - b);
  const p = (q) => Math.round(f[Math.min(f.length - 1, Math.floor(f.length * q))] || 0);
  return { frames: f.length, p50: p(0.5), p95: p(0.95), worst: Math.round(f[f.length - 1] || 0), over50: f.filter((x) => x > 50).length } }"""


def xy(page: Page, lon: float, lat: float) -> tuple[float, float]:
    # map.project() is relative to the map container; the mouse works in page coordinates (the header is above the map)
    p = page.evaluate("([lon, lat]) => { const q = window.__map.project([lon, lat]); const r = window.__map.getContainer().getBoundingClientRect(); return [q.x + r.left, q.y + r.top] }", [lon, lat])
    return p[0], p[1]


def settle(page: Page, ms: int = 2600) -> None:
    page.wait_for_timeout(ms)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 1440, "height": 810}, locale="ru-RU")
        log: list[str] = []
        page.on("console", lambda m: log.append(f"console.{m.type}: {m.text[:160]}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: log.append(f"pageerror: {e}"))
        page.on("framenavigated", lambda f: log.append(f"navigated: {f.url}") if f == page.main_frame else None)
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_function("window.__map && window.__map.getLayer('lvl-country-bubble') && window.__map.isStyleLoaded()", timeout=90000)
        settle(page, 3000)
        report = {}
        report["1 planet"] = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "levels_1_planet.png"))

        # wheel in at the planet: must stop at the level's max and show the hint
        page.mouse.move(720, 405)
        for _ in range(25):
            page.mouse.wheel(0, -120); page.wait_for_timeout(40)
        settle(page, 800)
        report["1 planet after wheel-in"] = page.evaluate(STATE) | {"hint": page.locator("text=Ближе").count()}

        # double click on Kazakhstan's bubble
        kz = page.evaluate("() => fetch('/geo/countries.json').then((r) => r.json()).then((l) => l.find((c) => c.iso === 'KZ'))")
        page.evaluate("([lon, lat]) => window.__map.jumpTo({ center: [lon, lat], zoom: 2.4 })", [kz["center"][1], kz["center"][0]])
        settle(page, 800)
        x, y = xy(page, kz["center"][1], kz["center"][0])
        page.evaluate(FRAMES_ON)
        page.mouse.dblclick(x, y)
        settle(page, 3000)
        print(chr(10).join(log[-15:]), flush=True)
        report["2 country (fly)"] = page.evaluate(FRAMES_OFF)
        report["2 country"] = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "levels_2_country.png"))

        # hover a city bubble → flat card; wheel at the country: panning + zoom within limits
        kzf = page.evaluate("() => fetch('/geo/c/KZ.json').then((r) => r.json())")
        almaty = kzf["cities"][0]
        x, y = xy(page, almaty["lon"], almaty["lat"])
        page.mouse.move(x - 30, y - 30); page.mouse.move(x, y, steps=6); settle(page, 500)
        report["2 country hover Almaty"] = page.evaluate(STATE)["popup"]
        page.evaluate(FRAMES_ON)
        page.mouse.dblclick(x, y)
        settle(page, 2600)
        report["3 city (fly)"] = page.evaluate(FRAMES_OFF)
        report["3 city"] = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "levels_3_city.png"))

        # click a university pin → selected card + side card
        uni = next(u for u in kzf["unis"] if u["c"] == almaty["id"])
        x, y = xy(page, uni["lon"], uni["lat"])
        page.mouse.click(x, y)
        settle(page, 1200)
        report["3 city selected"] = {"popup": page.evaluate(STATE)["popup"], "side card": page.locator("text=Профиль").count()}
        page.screenshot(path=str(OUT / "levels_3_selected.png"))

        # pan + zoom at the city level (frame times) and the zoom ceiling
        page.evaluate(FRAMES_ON)
        page.mouse.move(720, 405)
        page.mouse.down(); page.mouse.move(520, 300, steps=25); page.mouse.up()
        for _ in range(30):
            page.mouse.wheel(0, -120); page.wait_for_timeout(35)
        settle(page, 1200)
        report["3 city pan+zoom"] = page.evaluate(FRAMES_OFF) | page.evaluate(STATE)
        page.screenshot(path=str(OUT / "levels_3_zoomed.png"))

        # wheel out past the bottom of the city level → back to the country
        for _ in range(110):
            page.mouse.wheel(0, 150); page.wait_for_timeout(30)
        settle(page, 2800)
        report["up from city"] = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "levels_4_up.png"))
        # breadcrumb → planet
        page.locator("nav .crumb", has_text="Планета").first.click()
        settle(page, 3000)
        report["crumb → planet"] = page.evaluate(STATE)

        # search → spiral to the university's city with it selected
        box = page.get_by_placeholder("Например").first
        box.click(); box.type("Назарбаев", delay=30)
        item = page.get_by_text("Назарбаев Университет", exact=True).first
        item.wait_for()
        page.evaluate(FRAMES_ON)
        item.click()
        settle(page, 5500)
        report["search (fly)"] = page.evaluate(FRAMES_OFF)
        report["search → city"] = page.evaluate(STATE) | {"side card": page.locator("text=Профиль").count(), "lvl": page.evaluate("() => window.__lvl()")}
        page.screenshot(path=str(OUT / "levels_5_search.png"))

        # campus: cloud dive → photo scene
        try:
            page.get_by_role("button", name="Кампус").first.click(timeout=5000)
        except Exception as e:  # noqa: BLE001
            report["campus button"] = f"not found: {str(e)[:80]}"
            print(json.dumps(report, ensure_ascii=False, indent=1)); browser.close(); return
        settle(page, 5000)
        report["campus"] = {"lvl": page.evaluate("() => window.__lvl()"), "scene": page.locator("text=Так выглядит кампус").count()}
        page.screenshot(path=str(OUT / "levels_6_campus.png"))
        browser.close()
    print(chr(10).join(log[-20:]))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
