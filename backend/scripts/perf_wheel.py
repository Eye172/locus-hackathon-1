"""Manual zoom with the mouse wheel from the globe into Kazakhstan: frame times with and without the CSS cloud layer.

    python scripts/perf_wheel.py
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173/"
ARM = """() => { window.__f = []; let last = performance.now(); window.__run = true
  const tick = (t) => { window.__f.push(t - last); last = t; if (window.__run) requestAnimationFrame(tick) }; requestAnimationFrame(tick) }"""
REPORT = """() => { window.__run = false; const f = window.__f.slice(2).sort((a, b) => a - b)
  const p = (q) => Math.round(f[Math.min(f.length - 1, Math.floor(f.length * q))])
  return { frames: f.length, p50: p(0.5), p95: p(0.95), p99: p(0.99), worst: Math.round(f[f.length - 1]), over50: f.filter((x) => x > 50).length,
           zoom: +window.__map.getZoom().toFixed(2) } }"""


def run(label: str, args: list[str], hide_clouds: bool) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=args)
        page = browser.new_page(viewport={"width": 1440, "height": 810}, device_scale_factor=1.5)
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_function("window.__map && window.__map.getLayer('unis-point')", timeout=90000)
        if hide_clouds:
            page.add_style_tag(content=".clouds{display:none!important}")
        page.evaluate("() => window.__map.jumpTo({ center: [70, 49], zoom: 2.2 })")
        page.wait_for_timeout(3500)
        page.mouse.move(720, 405)
        page.evaluate(ARM)
        for _ in range(70):  # ~7 s of steady wheel zoom-in
            page.mouse.wheel(0, -60)
            page.wait_for_timeout(100)
        page.wait_for_timeout(500)
        r = page.evaluate(REPORT)
        print(f"{label:34} clouds {'off' if hide_clouds else 'on '} | {r}", flush=True)
        browser.close()


if __name__ == "__main__":
    gpu = ["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"]
    run("GPU", gpu, False)
    run("GPU", gpu, True)
    run("software (weak laptop)", ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"], False)
    run("software (weak laptop)", ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"], True)
