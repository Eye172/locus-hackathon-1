"""Frame times of the real click → spiral dive → clouds → reveal, on the GPU and in software rendering.

    python scripts/perf_dive.py
"""
from __future__ import annotations

import json

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173/"
ARM_JS = """() => {
  window.__frames = []; window.__long = []
  let last = performance.now()
  const tick = (t) => { window.__frames.push([Math.round(t - window.__t0), Math.round(t - last)]); last = t; requestAnimationFrame(tick) }
  new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__long.push([Math.round(e.startTime - window.__t0), Math.round(e.duration)]) }).observe({ type: 'longtask' })
  window.__t0 = performance.now(); requestAnimationFrame(tick)
}"""
REPORT_JS = """() => {
  const f = window.__frames.filter((x) => x[0] > 0 && x[0] < 9000)
  const bins = {}
  for (const [t, d] of f) { const k = Math.floor(t / 1000); (bins[k] ||= []).push(d) }
  const perSec = Object.entries(bins).map(([s, ds]) => ({ s: +s, fps: ds.length, worst: Math.max(...ds) }))
  return { perSec, bigFrames: f.filter((x) => x[1] > 50), long: window.__long.filter((x) => x[0] > 0) }
}"""


def run(label: str, args: list[str]) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=args)
        page = browser.new_page(viewport={"width": 1440, "height": 810}, locale="ru-RU")
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_function("window.__map && window.__map.getLayer('unis-point')", timeout=90000)
        page.wait_for_timeout(4000)
        box = page.get_by_placeholder("Например").first
        box.click(); box.type("Назарбаев", delay=30)
        item = page.get_by_text("Назарбаев Университет", exact=True).first
        item.wait_for()
        page.evaluate(ARM_JS)
        item.click()
        page.wait_for_timeout(9500)
        r = page.evaluate(REPORT_JS)
        print(f"\n== {label}")
        print("per second (fps, worst frame ms):", " ".join(f"{x['s']}s:{x['fps']}/{x['worst']}" for x in r["perSec"]))
        print("frames > 50 ms [t, ms]:", r["bigFrames"][:20])
        print("long tasks [t, ms]:", r["long"][:20], flush=True)
        browser.close()


if __name__ == "__main__":
    run("GPU (RTX, D3D11)", ["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
    run("software (SwiftShader) - weak laptop", ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
