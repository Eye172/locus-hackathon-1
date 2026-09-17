"""The whole arrival on the globe in GPU Chromium: search → spin → cloud dive → Google 3D orbit → city map → profile.
Saves a numbered frame series per university.

    python scripts/check_arrival.py "Назарбаев Университет" "Chinese University of Hong Kong"
    python scripts/check_arrival.py --base http://localhost:5180 --out ../docs/video/_rec/arrival "КазНУ"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower().encode("ascii", "ignore").decode()) or f"u{abs(hash(s)) % 10000}"


def run(page: Page, base: str, query: str, out: Path) -> dict:
    res: dict = {"query": query}
    name = slug(query)
    shot = lambda tag: page.screenshot(path=str(out / f"{name}_{tag}.png"))  # noqa: E731
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    box = page.locator("input").first
    box.fill(query)
    first = page.locator("button:has(span.font-mono)").first
    first.wait_for(timeout=15000)
    res["picked"] = first.inner_text().replace("\n", " | ")[:120]
    t0 = time.time()
    first.click()
    for i in range(10):  # spin, approach, clouds, white-out
        page.wait_for_timeout(1000)
        shot(f"a{i:02d}")
    skip = page.get_by_role("button", name=re.compile("Пропустить|Skip"))
    try:
        skip.wait_for(timeout=20000)
        res["orbit_after_s"] = round(time.time() - t0, 1)
        page.wait_for_timeout(2500)
        shot("b_orbit1")
        page.wait_for_timeout(5000)
        shot("b_orbit2")
        skip.click()
    except Exception as e:  # noqa: BLE001
        res["no_orbit"] = type(e).__name__
    page.wait_for_timeout(4000)
    shot("c_city")
    res["top_card"] = page.locator("button:has(.caps)").first.inner_text().replace("\n", " | ")[:200] if page.locator("button:has(.caps)").count() else None
    lock = page.get_by_text(re.compile("Карта города|City map"))
    res["lock"] = lock.first.inner_text() if lock.count() else None
    more = page.get_by_role("button", name=re.compile("Слои и расстояния|Layers and distances"))
    if more.count():
        more.first.click()
        page.wait_for_timeout(1500)
        shot("d_drawer")
        res["drawer"] = page.locator("aside").first.inner_text().replace("\n", " | ")[:300]
        more.first.click()
    # zoom far out: the camera must stay over the city
    page.mouse.move(900, 450)
    for _ in range(25):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(80)
    page.wait_for_timeout(3500)
    shot("e_wheel_out")
    res["after_wheel_range"] = page.evaluate("() => Math.round(document.querySelector('gmp-map-3d')?.range ?? -1)")
    # try to leave: the camera must stay above the city (bounds + max altitude), the rest of the world is dimmed
    res["far"] = page.evaluate("""async () => {
      const m = document.querySelector('gmp-map-3d')
      const c = m.center
      m.flyCameraTo({ endCamera: { center: { lat: c.lat + 1.5, lng: c.lng + 1.5, altitude: c.altitude }, range: 400000, tilt: 35, heading: m.heading }, durationMillis: 1500 })
      await new Promise((r) => setTimeout(r, 2600))
      const cp = m.cameraPosition
      return { range: Math.round(m.range), camAlt: cp && Math.round(cp.altitude), camLat: cp && +cp.lat.toFixed(3), camLng: cp && +cp.lng.toFixed(3), maxAltitude: m.maxAltitude, bounds: m.bounds && JSON.stringify(m.bounds) }
    }""")
    page.wait_for_timeout(3000)
    shot("f_far")
    card = page.locator("button:has(.caps)").first
    card.click()
    page.wait_for_timeout(2500)
    res["after_card_url"] = page.url
    res["total_s"] = round(time.time() - t0, 1)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="+")
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "video" / "_rec" / "arrival"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True,
                                     args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 860}, device_scale_factor=1, locale="ru-RU")
        ctx.add_init_script("try { localStorage.setItem('campuslens.lang', 'ru') } catch (e) {}")
        for q in a.queries:
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text[:160]) if m.type in ("error", "warning") and "429" not in m.text else None)
            try:
                r = run(page, a.base, q, out)
            except Exception as e:  # noqa: BLE001
                page.screenshot(path=str(out / f"{slug(q)}_x_error.png"))
                r = {"query": q, "exception": f"{type(e).__name__}: {e}"[:300]}
            r["console"] = errors[:6]
            print(json.dumps(r, ensure_ascii=False, indent=1), flush=True)
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
