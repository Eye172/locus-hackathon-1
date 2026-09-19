"""What costs frame time in the settled campus scene: orbit at a fixed camera, switch parts off one by one.

    python ablate.py Q2783344 [--gpu low|high] [--base http://localhost:5173]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from playwright.sync_api import sync_playwright

INIT = r"""
(() => {
  window.__fr = []
  let last = 0
  const tick = (now) => { if (last) window.__fr.push([now, now - last]); last = now; requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
})()
"""

MEASURE = r"""
async (ms) => {
  const m = document.querySelector('gmp-map-3d')
  m.stopCameraAnimation?.()
  m.flyCameraAround({ camera: { center: m.center, range: m.range, tilt: m.tilt, heading: m.heading }, durationMillis: 40000, repeatCount: 1 })
  await new Promise((r) => setTimeout(r, 700))
  const a = performance.now()
  await new Promise((r) => setTimeout(r, ms))
  const b = performance.now()
  m.stopCameraAnimation?.()
  const fr = window.__fr.filter((f) => f[0] >= a && f[0] < b).map((f) => f[1]).sort((x, y) => x - y)
  const q = (p) => +fr[Math.min(fr.length - 1, Math.floor(fr.length * p))].toFixed(1)
  return { fps: +(fr.length / ((b - a) / 1000)).toFixed(0), p50: q(0.5), p95: q(0.95), max: +fr[fr.length - 1].toFixed(0) }
}
"""

STEPS = [
    ("baseline", "() => {}"),
    ("- maplibre canvas hidden", "() => { for (const c of document.querySelectorAll('.maplibregl-map')) c.parentElement.style.display = 'none' }"),
    ("- backdrop-filter off", "() => { const s = document.createElement('style'); s.textContent = '*{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}'; document.head.append(s) }"),
    ("- vignette + clouds layers off", "() => { for (const e of document.querySelectorAll('.clouds')) e.parentElement.style.display = 'none'; for (const e of document.querySelectorAll('[class*=radial-gradient]')) e.style.display = 'none' }"),
    ("- html markers removed", "() => { for (const e of document.querySelectorAll('gmp-marker-interactive, gmp-marker')) e.remove() }"),
    ("- grey models removed", "() => { for (const e of document.querySelectorAll('gmp-model-3d')) e.remove() }"),
    ("- campus/dorm polygons removed", "() => { for (const e of document.querySelectorAll('gmp-polygon-3d-interactive')) e.remove() }"),
    ("- city mask + lines removed", "() => { for (const e of document.querySelectorAll('gmp-polygon-3d, gmp-polyline-3d')) e.remove() }"),
]

GPU = r"""
() => { const c = document.createElement('canvas'); const gl = c.getContext('webgl'); const x = gl && gl.getExtension('WEBGL_debug_renderer_info');
  return x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : 'unknown' }
"""

COUNT = r"""
() => { const m = document.querySelector('gmp-map-3d'); const n = {}; for (const c of m.children) n[c.localName] = (n[c.localName] ?? 0) + 1; return { n, range: Math.round(m.range), tilt: Math.round(m.tilt) } }
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--gpu", choices=["low", "high", "auto"], default="auto")
    ap.add_argument("--range", type=float, default=0)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args = ["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"]
    if a.gpu == "low":
        args.append("--force_low_power_gpu")
    if a.gpu == "high":
        args.append("--force_high_performance_gpu")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=args)
        ctx = browser.new_context(viewport={"width": 1707, "height": 930}, device_scale_factor=1.5, locale="ru-RU")
        ctx.add_init_script(INIT)
        page = ctx.new_page()
        page.goto(f"{a.base}/?u={a.qid}", wait_until="domcontentloaded")
        print("gpu:", page.evaluate(GPU), flush=True)
        skip = page.get_by_role("button", name=re.compile("Пропустить|Skip"))
        skip.wait_for(timeout=60000)
        page.wait_for_timeout(3000)
        try:
            skip.click(timeout=5000)
        except Exception as e:
            print("skip click failed", type(e).__name__, flush=True)
        page.wait_for_timeout(12000)   # places, dorms, the city lock, grey chunks
        if a.range:
            page.evaluate("(r) => { document.querySelector('gmp-map-3d').range = r }", a.range)
            page.wait_for_timeout(6000)
        print(json.dumps(page.evaluate(COUNT), ensure_ascii=False), flush=True)
        for name, js in STEPS:
            page.evaluate(js)
            page.wait_for_timeout(600)
            r = page.evaluate(MEASURE, 3500)
            print(f"{name:38s} {json.dumps(r)}", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
