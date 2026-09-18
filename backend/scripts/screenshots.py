"""Capture UI screenshots for the pitch deck and README (headless Chromium via Playwright).

    python scripts/screenshots.py [base_url]   # default http://localhost:5173

Writes PNGs to docs/screens/. Requires: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parents[2] / "docs" / "screens"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"
QID = "Q2783344"

SHOTS = [
    ("globe", "/", 9000, None),
    ("profile", f"/u/{QID}", 5000, None),
    ("passport", f"/u/{QID}?photo=c38f8b42e2a4ceb6", 5000, None),
    ("map3d", f"/?u={QID}", 18000, None),  # the main map flies to the campus
    ("climate", f"/u/{QID}?tab=climate", 7000, None),
    ("city", f"/u/{QID}?tab=city", 6000, None),
    ("bvr", f"/u/{QID}?tab=bvr", 4000, None),
    ("rejected", f"/u/{QID}?tab=rejected", 4000, None),
    ("judge", f"/u/{QID}?tab=judge", 4000, None),
    ("compare", f"/compare?a={QID}&b=Q427677", 12000, None),
]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = await browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
        for name, path, wait, _ in SHOTS:
            try:
                await page.goto(BASE + path, wait_until="networkidle", timeout=60000)
            except Exception:
                pass
            await page.wait_for_timeout(wait)
            await page.screenshot(path=str(OUT / f"{name}.png"))
            print("saved", name)
        # fly-to sequence on the globe
        await page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(8000)
        await page.fill("input", "Nazarbayev University")
        await page.wait_for_timeout(1800)
        buttons = page.locator("button", has_text="Назарбаев Университет")
        if await buttons.count():
            await buttons.first.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT / "flight-clouds.png"))
            await page.wait_for_timeout(3500)
            await page.screenshot(path=str(OUT / "flight-arrival.png"))
            print("saved flight-clouds, flight-arrival")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
