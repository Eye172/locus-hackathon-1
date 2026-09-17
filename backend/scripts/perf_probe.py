"""Where does the globe stutter? Drives the app in Chromium (real GPU when available) and measures frame times and
long tasks for a few camera moves, with different layer groups switched off.

    python scripts/perf_probe.py
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173/"

MEASURE_JS = """
async ({ hide, move }) => {
  const m = window.__map
  const style = m.getStyle()
  const groups = {
    unis: style.layers.filter((l) => l.id.startsWith('unis')).map((l) => l.id),
    vector: style.layers.filter((l) => l.type !== 'raster' && l.type !== 'background' && !l.id.startsWith('unis')).map((l) => l.id),
    esri: ['esri'],
  }
  for (const g of hide) for (const id of groups[g] || []) if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', 'none')
  m.jumpTo({ center: [66, 44], zoom: 1.5, pitch: 0, bearing: 0 })
  await new Promise((r) => m.once('idle', r))
  const long = []
  const obs = new PerformanceObserver((l) => { for (const e of l.getEntries()) long.push(e.duration) })
  obs.observe({ type: 'longtask' })
  const frames = []
  let last = performance.now(), run = true
  const tick = (t) => { frames.push(t - last); last = t; if (run) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  const t0 = performance.now()
  await new Promise((resolve) => {
    const [lon, lat, z1, ms, pitch] = move
    const step = (now) => {
      const u = Math.min(1, (now - t0) / ms)
      const e = u * u * (3 - 2 * u)
      m.jumpTo({ center: [66 + (lon - 66) * e, 44 + (lat - 44) * e], zoom: 1.5 + (z1 - 1.5) * e, pitch: pitch * e })
      if (u < 1) requestAnimationFrame(step); else resolve()
    }
    requestAnimationFrame(step)
  })
  const tIdle = performance.now()
  await new Promise((r) => { const to = setTimeout(r, 8000); m.once('idle', () => { clearTimeout(to); r() }) })
  const settle = performance.now() - tIdle
  run = false; obs.disconnect()
  for (const g of hide) for (const id of groups[g] || []) if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', 'visible')
  frames.sort((a, b) => a - b)
  const p = (q) => Math.round(frames[Math.min(frames.length - 1, Math.floor(frames.length * q))])
  return { frames: frames.length, p50: p(0.5), p95: p(0.95), worst: Math.round(frames[frames.length - 1]),
           over50: frames.filter((f) => f > 50).length, longTaskMs: Math.round(long.reduce((a, b) => a + b, 0)),
           longTasks: long.length, settleMs: Math.round(settle) }
}
"""


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True,
                                     args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-features=Vulkan"])
        page = browser.new_page(viewport={"width": 1440, "height": 810})
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_function("window.__map && window.__map.getLayer('unis-point')", timeout=60000)
        page.wait_for_timeout(3000)
        gpu = page.evaluate("""() => { const gl = document.createElement('canvas').getContext('webgl');
          const d = gl && gl.getExtension('WEBGL_debug_renderer_info'); return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'n/a' }""")
        print("GPU:", gpu, flush=True)
        moves = {
            "globe→Astana z12 (4s)": [71.43, 51.13, 12, 4000, 0],
            "globe→Astana z16 pitch60 (4s)": [71.43, 51.13, 16, 4000, 60],
        }
        variants = [("all layers", []), ("no uni markers", ["unis"]), ("no vector basemap", ["vector"]),
                    ("rasters only", ["unis", "vector"]), ("no esri imagery", ["esri"])]
        for mname, move in moves.items():
            for vname, hide in variants:
                r = page.evaluate(MEASURE_JS, {"hide": hide, "move": move})
                print(f"{mname:32} | {vname:18} | {json.dumps(r)}", flush=True)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
