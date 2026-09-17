"""Visual check of the Google 3D campus page (/map3d/{qid}) in GPU Chromium: overview, dormitories, a selected
place and all layers on, for any list of universities (random ones from the index by default).

    python scripts/check_map3d.py Q2783344 Q309331          # given universities
    python scripts/check_map3d.py --random 5 --seed 42     # random universities with coordinates
    python scripts/check_map3d.py --base http://localhost:5180 --out ../docs/screens/map3d
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]

WAIT_JS = """
async ({ timeout }) => {
  const m = document.querySelector('gmp-map-3d')
  if (!m) return 'no-map'
  const t0 = performance.now()
  // wait for the camera to stop, then for the tiles to settle
  await new Promise((r) => {
    let last = JSON.stringify([m.center, m.range, m.heading, m.tilt])
    const tick = () => {
      const now = JSON.stringify([m.center, m.range, m.heading, m.tilt])
      if (now === last || performance.now() - t0 > timeout) r(); else { last = now; setTimeout(tick, 400) }
    }
    setTimeout(tick, 400)
  })
  const steady = await new Promise((r) => {
    const to = setTimeout(() => r('timeout'), timeout)
    const on = (e) => { if (e.isSteady) { clearTimeout(to); m.removeEventListener('gmp-steadychange', on); r('steady') } }
    m.addEventListener('gmp-steadychange', on)
    // already steady: nudge nothing, just give it a moment
    setTimeout(() => { clearTimeout(to); r('assumed') }, Math.min(timeout, 6000))
  })
  return `${steady} ${Math.round(performance.now() - t0)}ms children=${m.children.length}`
}
"""


def settle(page: Page, timeout_ms: int = 20000) -> str:
    return page.evaluate(WAIT_JS, {"timeout": timeout_ms})


def pick_random(n: int, seed: int) -> list[str]:
    rows = json.loads((ROOT / "data" / "universities.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    rows = [r for r in rows if r.get("coord")]
    return [r["id"] for r in rng.sample(rows, n)]


def open_sheet(page: Page) -> None:
    """Phones: unfold the bottom sheet before touching the layer list."""
    handle = page.locator("aside button[aria-label=toggle]")
    if handle.count() and handle.is_visible():
        handle.click()
        page.wait_for_timeout(500)


def check(page: Page, base: str, qid: str, out: Path) -> dict:
    res: dict = {"qid": qid}
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    t0 = time.time()
    page.goto(f"{base}/map3d/{qid}", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("gmp-map-3d", timeout=45000)
    except Exception:  # noqa: BLE001
        page.screenshot(path=str(out / f"{qid}_0_fail.png"))
        res["error"] = page.inner_text("body")[:300]
        return res
    page.wait_for_timeout(3500)  # the intro flight
    res["settle"] = settle(page)
    res["ready_s"] = round(time.time() - t0, 1)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out / f"{qid}_1_overview.png"))
    aside = page.locator("aside")
    res["title"] = aside.locator("h1").inner_text()
    res["facts"] = aside.locator("div.border-b").nth(1).inner_text().replace("\n", " | ")[:400]

    dorms = page.get_by_role("button", name="Общежития").first
    if dorms.is_enabled():
        dorms.click()
        page.wait_for_timeout(2200)
        settle(page, 12000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(out / f"{qid}_2_dorms.png"))

    # open the café list and pick the nearest one
    open_sheet(page)
    row = aside.get_by_text("Кафе и кофейни", exact=True)
    if row.count():
        row.first.click()
        page.wait_for_timeout(400)
        items = aside.locator("ul li button")
        if items.count():
            res["first_cafe"] = items.first.inner_text().replace("\n", " | ")
            items.first.click()
            page.wait_for_timeout(2400)
            settle(page, 12000)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / f"{qid}_3_selected.png"))
            card = page.locator("div.card.p-4")
            res["card"] = card.inner_text().replace("\n", " | ")[:300] if card.count() else None

    # two more layers on (each one is a Places request), back to the campus
    open_sheet(page)
    for label in ("Развлечения", "Культура: музеи, театры"):
        aside.locator("div.flex.items-center", has_text=label).locator("button[role=switch]").first.click()
    page.get_by_role("button", name="Кампус", exact=True).click()
    page.wait_for_timeout(2500)
    settle(page, 15000)
    page.wait_for_timeout(2500)
    page.screenshot(path=str(out / f"{qid}_4_more.png"))
    res["layers"] = aside.locator("div.px-2.py-2").inner_text().replace("\n", " | ")[:500]
    res["errors"] = errors[:8]
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qids", nargs="*")
    ap.add_argument("--random", type=int, default=0)
    ap.add_argument("--seed", type=int, default=int(time.time()))
    ap.add_argument("--base", default="http://localhost:5180")
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "video" / "_rec" / "map3d"))
    ap.add_argument("--mobile", action="store_true")
    a = ap.parse_args()
    qids = a.qids + (pick_random(a.random, a.seed) if a.random else [])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True,
                                     args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        vp = {"width": 390, "height": 844} if a.mobile else {"width": 1440, "height": 860}
        ctx = browser.new_context(viewport=vp, device_scale_factor=1, locale="ru-RU")
        ctx.add_init_script("try { localStorage.setItem('campuslens.lang', 'ru') } catch (e) {}")
        for qid in qids:
            page = ctx.new_page()
            try:
                r = check(page, a.base, qid, out)
            except Exception as e:  # noqa: BLE001
                page.screenshot(path=str(out / f"{qid}_x_error.png"))
                r = {"qid": qid, "exception": f"{type(e).__name__}: {e}"[:300]}
            print(json.dumps(r, ensure_ascii=False, indent=1), flush=True)
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
