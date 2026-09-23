"""Load and idle cost of the built site, as a user's browser sees it: the globe at rest and a profile page.

    python scripts/perf_pages.py                                   # the built app served by the backend on :8000
    python scripts/perf_pages.py --base http://127.0.0.1:8020 --base old=http://127.0.0.1:8030 --runs 3

Each --base is `name=url` or a url; several of them are compared side by side (e.g. a checkout of the previous commit
on another port, started with DATA_DIR pointing at the same data). Two machines: "desktop" and "weak" (CPU 4x slower,
10 Mbit/s, 40 ms RTT). Every run starts with an empty cache.
  globe    download size; then 6 s of the idle planet: MapLibre frames/s, main-thread busy ms/s, long tasks
  profile  /u/<qid>?tab=photos. The live build is replaced by a fake EventSource that replays the saved profile three
           times (as the cached, live and final events do), so no server build starts: download size, LCP, CLS, total
           blocking time, main-thread time over 8 s
"""
from __future__ import annotations

import argparse
import json
import statistics

from playwright.sync_api import sync_playwright

INIT = """
window.__perf = { lcp: 0, long: [], cls: 0 };
new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.lcp = e.startTime }).observe({ type: 'largest-contentful-paint', buffered: true });
new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.long.push([e.startTime, e.duration]) }).observe({ type: 'longtask', buffered: true });
new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value }).observe({ type: 'layout-shift', buffered: true });
(() => {
  const Real = window.EventSource;
  window.EventSource = class {
    constructor(url) {
      this.ls = {}; this.closed = false;
      const m = /\\/api\\/profile\\/([^/]+)\\/stream/.exec(url);
      if (!m) return new Real(url);
      fetch('/api/profile/' + m[1]).then((r) => r.text()).then((txt) => {
        const send = (final, cached, delay) => setTimeout(() => {
          if (this.closed) return;
          const data = JSON.stringify({ type: 'profile', profile: JSON.parse(txt), cached, final, elapsed_ms: 0 });
          (this.ls['profile'] || []).forEach((f) => f({ data }));
        }, delay);
        send(false, true, 0); send(false, false, 1500); send(true, false, 3000);
      });
    }
    addEventListener(t, f) { (this.ls[t] = this.ls[t] || []).push(f) }
    close() { this.closed = true }
  };
})();
"""


def open_page(ctx, url: str, weak: bool):
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Performance.enable")
    if weak:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 40, "downloadThroughput": 10e6 / 8,
                                                      "uploadThroughput": 5e6 / 8})
    sizes: dict[str, int] = {}
    cdp.on("Network.loadingFinished", lambda e: sizes.__setitem__(e["requestId"], e["encodedDataLength"]))
    page.add_init_script(INIT)
    page.goto(url, wait_until="load", timeout=120000)
    return page, cdp, sizes


def metrics(cdp) -> dict[str, float]:
    return {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


def globe(ctx, base: str, weak: bool, _qid: str) -> dict:
    page, cdp, sizes = open_page(ctx, base + "/", weak)
    page.wait_for_function("window.__map && window.__map.getLayer('unis-point')", timeout=120000)
    page.wait_for_timeout(12000)   # the opening shot and the first tiles are over
    m0 = metrics(cdp)
    r = page.evaluate("""async () => { const m = window.__map; let n = 0; const f = () => n++; m.on('render', f);
        const long0 = window.__perf.long.length;
        await new Promise((res) => setTimeout(res, 6000)); m.off('render', f);
        return { renders: n / 6, long: window.__perf.long.length - long0 } }""")
    m1 = metrics(cdp)
    page.close()
    return {"loadKB": round(sum(sizes.values()) / 1024), "framesPerSec": round(r["renders"], 1),
            "busyMsPerSec": round((m1["TaskDuration"] - m0["TaskDuration"]) * 1000 / 6, 1), "longTasks": r["long"]}


def profile(ctx, base: str, weak: bool, qid: str) -> dict:
    page, cdp, sizes = open_page(ctx, f"{base}/u/{qid}?tab=photos", weak)
    page.wait_for_timeout(8000)   # the three profile events at 0 / 1.5 / 3 s, then quiet
    m1 = metrics(cdp)
    p = page.evaluate("""() => { const long = window.__perf.long; return { lcp: window.__perf.lcp, cls: window.__perf.cls,
        tbt: long.reduce((a, b) => a + Math.max(0, b[1] - 50), 0) } }""")
    page.close()
    return {"loadKB": round(sum(sizes.values()) / 1024), "lcpMs": round(p["lcp"]), "cls": round(p["cls"], 3),
            "tbtMs": round(p["tbt"]), "mainThreadMs": round(m1["TaskDuration"] * 1000)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", action="append", help="name=url or url (repeatable)")
    ap.add_argument("--qid", default="Q2783344", help="a university with a saved profile (default: NU, ~300 photos)")
    ap.add_argument("--runs", type=int, default=2, help="runs per case; the median is printed")
    args = ap.parse_args()
    bases = dict((b.split("=", 1) if "=" in b else (b, b)) for b in (args.base or ["http://127.0.0.1:8000"]))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True,
                                     args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
        for machine in ("desktop", "weak"):
            for test in (globe, profile):
                for name, url in bases.items():
                    runs = []
                    for _ in range(args.runs):
                        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                        runs.append(test(ctx, url, machine == "weak", args.qid))
                        ctx.close()
                    med = {k: statistics.median(r[k] for r in runs) for k in runs[0]}
                    print(f"{machine:8} {test.__name__:8} {name:28} {json.dumps(med)}", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
