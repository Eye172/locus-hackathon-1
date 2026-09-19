"""Long animation frames during the flight and the orbit, attributed to scripts.

    python loaf.py Q2783344 [--base http://localhost:5173] [--secs 14]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from playwright.sync_api import sync_playwright

INIT = r"""
(() => {
  window.__loaf = []
  window.__fr = []
  let last = 0
  const tick = (now) => { if (last) window.__fr.push([now, now - last]); last = now; requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  window.__adds = []
  const watch = () => {
    const m = document.querySelector('gmp-map-3d')
    if (!m) { setTimeout(watch, 50); return }
    window.__adds.push([Math.round(performance.now()), 'map-created', 1])
    new MutationObserver((recs) => { const n = {}; for (const r of recs) for (const x of r.addedNodes) n[x.localName] = (n[x.localName] ?? 0) + 1
      for (const k in n) window.__adds.push([Math.round(performance.now()), k, n[k]]) }).observe(m, { childList: true })
  }
  watch()
  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) {
        window.__loaf.push({ t: Math.round(e.startTime), d: Math.round(e.duration), block: Math.round(e.blockingDuration), render: Math.round(e.startTime + e.duration - e.renderStart),
          scripts: (e.scripts ?? []).map((s) => ({ d: Math.round(s.duration), inv: (s.invoker || '').slice(0, 80), fn: s.sourceFunctionName, src: (s.sourceURL || '').replace(/^https?:\/\/[^/]+/, '').slice(0, 70), type: s.invokerType, layout: Math.round(s.forcedStyleAndLayoutDuration) })) })
      }
    }).observe({ type: 'long-animation-frame', buffered: true })
  } catch (e) { window.__loafErr = String(e) }
})()
"""

SAT = r"""
(() => { const iv = setInterval(() => { const g = window.google?.maps; if (!g?.importLibrary || g.__w) return; g.__w = 1
  const orig = g.importLibrary.bind(g)
  g.importLibrary = async (n, ...r) => { const lib = await orig(n, ...r); if (n !== 'maps3d') return lib
    const P = new Proxy(lib.Map3DElement, { construct(t, args, nt) { args[0] = { ...args[0], mode: 'SATELLITE' }; const inst = Reflect.construct(t, args, nt)
      Object.defineProperty(inst, 'mode', { get() { return 'SATELLITE' }, set() {} }); window.__sat = (window.__sat || 0) + 1; return inst } })
    return new Proxy(lib, { get(t, k) { return k === 'Map3DElement' ? P : t[k] } }) } }, 2) })()
"""

REPORT = r"""
(args) => {
  const [a, b] = args
  const L = window.__loaf.filter((e) => e.t >= a && e.t < b)
  const by = {}
  for (const e of L) for (const s of e.scripts) {
    const host = /maps\/api|maps-api|gstatic|googleapis/.test(s.src + s.inv) ? 'google' : /maplibre/.test(s.src) ? 'maplibre' : /Campus3D/.test(s.src) ? 'Campus3D' : /GlobeMap|Globe\.tsx|CloudDive|darkTheme/.test(s.src) ? 'globe-code' : /react-dom|scheduler|chunk-/.test(s.src) ? 'react/vendor' : (s.src || s.inv || '?').slice(0, 40)
    const k = by[host] ??= { ms: 0, n: 0 }
    k.ms += s.d; k.n++
  }
  const fr = window.__fr.filter((f) => f[0] >= a && f[0] < b).map((f) => f[1]).sort((x, y) => x - y)
  return { frames: fr.length, fps: +(fr.length / ((b - a) / 1000)).toFixed(0), p50: +fr[Math.floor(fr.length * 0.5)].toFixed(1), p95: +fr[Math.floor(fr.length * 0.95)].toFixed(1), p99: +fr[Math.floor(fr.length * 0.99)].toFixed(1), max: Math.round(fr[fr.length - 1]),
    over50: fr.filter((x) => x > 50).length, over100: fr.filter((x) => x > 100).length,
    loaf_n: L.length, loaf_ms: L.reduce((s, e) => s + e.d, 0), by,
    worst: [...L].sort((x, y) => y.d - x.d).slice(0, 6).map((e) => ({ t: e.t, d: e.d, render: e.render, scripts: e.scripts.filter((s) => s.d >= 8).map((s) => `${s.d}ms ${s.type} ${s.fn || ''} ${s.src || s.inv}`) })) }
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--secs", type=float, default=14)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--search", default="")
    ap.add_argument("--satellite", action="store_true")
    ap.add_argument("--strip", default="", help="comma list: buildings, dorms, places")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=not a.headed,
                                     args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"] + (["--start-maximized"] if a.headed else []))
        ctx = browser.new_context(no_viewport=True, locale="ru-RU") if a.headed else browser.new_context(viewport={"width": 1707, "height": 930}, device_scale_factor=1.5, locale="ru-RU")
        ctx.add_init_script(INIT)
        if a.satellite:
            ctx.add_init_script(SAT)
        page = ctx.new_page()
        if a.strip:
            def strip(route):
                r = route.fetch()
                try:
                    d = r.json()
                    if "buildings" in a.strip:
                        d["campus"]["buildings"] = []
                    if "dorms" in a.strip:
                        d["dorms"] = []
                    if "places" in a.strip:
                        d["places"] = {}
                    route.fulfill(response=r, json=d)
                except Exception:  # noqa: BLE001
                    route.fulfill(response=r)
            page.route(re.compile(r"/api/map3d/[^/]+(\?.*)?$"), strip)
        page.bring_to_front()
        if a.search:
            page.goto(a.base + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)   # the opening shot, the planet settles
            page.locator("input").first.fill(a.search)
            first = page.locator("button:has(span.font-mono), [role=option], ul li button").first
            first.wait_for(timeout=15000)
            page.wait_for_timeout(1500)
            t0 = page.evaluate("() => performance.now()")
            first.click()
        else:
            page.goto(f"{a.base}/?u={a.qid}", wait_until="domcontentloaded")
            t0 = page.evaluate("() => performance.now()")
        skip = page.get_by_role("button", name=re.compile("Пропустить|Skip"))
        skip.wait_for(timeout=60000)
        t1 = page.evaluate("() => performance.now()")
        page.wait_for_timeout(int(a.secs * 1000))
        t2 = page.evaluate("() => performance.now()")
        print("FLIGHT", json.dumps(page.evaluate(REPORT, [t0, t1]), ensure_ascii=False, indent=1))
        print("ORBIT", json.dumps(page.evaluate(REPORT, [t1, t2]), ensure_ascii=False, indent=1))
        adds = page.evaluate("() => window.__adds")
        print("ADDS", " | ".join(f"{t}:{k}x{n}" for t, k, n in adds))
        print("sat maps:", page.evaluate("() => window.__sat || 0"))
        print("t0 =", round(t0), "; first orbit frame at", round(t1 - t0), "ms after start; loafErr:", page.evaluate("() => window.__loafErr || null"))
        browser.close()


if __name__ == "__main__":
    main()
