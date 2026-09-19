"""README screenshots of every feature, into docs/screens/NN_name.png.

Pages are shot whole (full page) only after they have loaded: scrolled to the end so lazy photos load, no skeletons
left, every <img> decoded. Profile streams are answered with the cached profile (no live build, no credits).
GPU Chromium, headed (the Google 3D map and Street View need a real GPU).

    python scripts/readme_shots.py [name ...]     # only these shots; servers on :8000 and :5173

Writes PNG; the README uses JPEG copies (quality 86).
"""
import asyncio, json, sys, time, urllib.parse, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:5173"
API = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parents[2] / "docs" / "screens"
NU, KBTU, STANFORD = "Q2783344", "Q1734762", "Q41506"
ONLY = sys.argv[1:]
_cache: dict[str, bytes] = {}


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return r.read()


MIT = "Q49108"


def prof(qid: str) -> bytes:
    if qid not in _cache:
        p = json.loads(get(f"/api/profile/{qid}"))
        _cache[qid] = f"event: profile\ndata: {json.dumps({'type': 'profile', 'profile': p, 'cached': True, 'final': True}, ensure_ascii=False)}\n\n".encode()
    return _cache[qid]


SEED = """
try {
  const now = new Date().toISOString();
  localStorage.setItem('campuslens.saved', JSON.stringify({
    '%s': { name: 'Nazarbayev University', city: 'Астана', savedAt: now, photos: 292 },
    '%s': { name: 'Stanford University', city: 'Стэнфорд', savedAt: now, photos: 317 },
    '%s': { name: 'Massachusetts Institute of Technology', city: 'Кембридж', savedAt: now, photos: 209 },
  }));
  localStorage.setItem('campuslens.visitPlan', JSON.stringify({ '%s': { items: [
    { id: 'a', label: 'Атриум главного корпуса', done: true }, { id: 'b', label: 'Библиотека NU', done: false },
    { id: 'c', label: 'Спорткомплекс, скалодром', done: false } ] } }));
} catch (e) {}
""" % (NU, STANFORD, MIT, NU)


async def settle(page, timeout=60.0):
    """Scroll to the end and back (lazy photos), then wait: no skeletons, every image decoded, network quiet."""
    for _ in range(2):
        h = await page.evaluate("document.documentElement.scrollHeight")
        y = 0
        while y < h:
            y += 650
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(220)
            h = await page.evaluate("document.documentElement.scrollHeight")
    await page.evaluate("window.scrollTo(0, 0)")
    t0 = time.time()
    while time.time() - t0 < timeout:
        busy = await page.evaluate("""(() => {
          const sk = document.querySelectorAll('.shimmer, .animate-spin').length;
          const im = [...document.images].filter(i => i.src && !(i.complete && i.naturalWidth > 0)).length;
          return sk + im; })()""")
        if not busy:
            break
        await page.wait_for_timeout(700)
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def main():
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chromium", headless=False, args=["--use-angle=d3d11", "--ignore-gpu-blocklist", "--window-position=0,0"])

        async def new_ctx(**kw):
            ctx = await b.new_context(**kw)
            await ctx.add_init_script(SEED)

            async def stream(route):
                qid = route.request.url.split("/api/profile/")[1].split("/")[0]
                await route.fulfill(status=200, headers={"content-type": "text/event-stream"}, body=prof(qid))
            await ctx.route("**/api/profile/*/stream*", stream)
            return ctx

        ctx = await new_ctx(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = await ctx.new_page()

        def want(name):
            return not ONLY or any(o in name for o in ONLY)

        async def save(name, full=False):
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name, flush=True)

        async def page_shot(name, path, full=True, then=None, wait=3000, timeout=60.0):
            if not want(name):
                return
            await page.goto(BASE + path, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait)
            if then:
                await then()
            await settle(page, timeout)
            await save(name, full)

        # ---- planet, search
        if want("01_globe"):
            await page.goto(BASE + "/", wait_until="domcontentloaded")
            await page.wait_for_timeout(10000)
            await save("01_globe")
        if want("02_search"):
            await page.goto(BASE + "/", wait_until="domcontentloaded")
            await page.wait_for_timeout(8000)
            await page.locator("input").first.fill("Назарбаев")
            await page.wait_for_timeout(3500)
            await save("02_search")

        # ---- flight and the 3D campus
        async def arrival(name, qid, series=False, drawer=False):
            if not want(name):
                return
            await page.goto(f"{BASE}/?u={qid}", wait_until="domcontentloaded")
            t0 = time.time()
            if series:
                for at, n in [(0.8, "03_flight_spin"), (3.2, "04_flight_clouds")]:
                    await page.wait_for_timeout(max(0, int((t0 + at - time.time()) * 1000)))
                    await save(n)
            skip = page.get_by_role("button", name="Пропустить")
            try:
                await skip.wait_for(timeout=40000)
                await page.wait_for_timeout(2500)
                await skip.click()
            except Exception:
                pass
            await page.wait_for_timeout(16000)   # the camera settles and the detailed tiles come in
            await save(name)
            if drawer:
                bar = page.locator("div.bottom-3 button").first
                await bar.click()
                await page.wait_for_timeout(4000)
                await save(name + "_panel")

        await arrival("05_3d_stanford", STANFORD)
        await arrival("06_3d_mit", MIT)
        await arrival("07_3d_nu", NU, series=True, drawer=True)

        # ---- profile, every tab whole
        await page_shot("08_profile", f"/u/{NU}")
        await page_shot("09_photos_albums", f"/u/{NU}?tab=photos")

        async def filt():
            await page.get_by_role("button", name="Общежитие").first.click()
            await page.wait_for_timeout(1500)
        await page_shot("10_photos_filter_dorm", f"/u/{NU}?tab=photos", then=filt)

        async def bvr():
            await page.locator(".seg button").nth(2).click()
            await page.wait_for_timeout(1500)
        await page_shot("11_photos_brochure_reality", f"/u/{NU}?tab=photos", then=bvr)

        async def passport():
            await settle(page, 30)
            await page.locator("main button img").first.click()
            await page.wait_for_timeout(4000)
        if want("12_photo_passport"):
            await page.goto(f"{BASE}/u/{NU}?tab=photos", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            await passport()
            await save("12_photo_passport")

        await page_shot("13_campus", f"/u/{NU}?tab=campus", timeout=90)
        await page_shot("14_city", f"/u/{NU}?tab=city")
        await page_shot("15_climate", f"/u/{NU}?tab=climate", timeout=90)
        await page_shot("16_verify", f"/u/{NU}?tab=verify")

        # ---- «Обзор»: 3D photos and the Street View walk
        if want("17_tour"):
            await page.goto(f"{BASE}/u/{NU}", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            await page.get_by_role("button", name="Обзор", exact=True).first.click()
            await page.wait_for_timeout(7000)
            await save("17_tour_3d_photos")
            await page.get_by_role("button", name="Прогулка").click()
            await page.wait_for_timeout(9000)
            await save("18_tour_walk_street")
            await page.get_by_role("button", name="Внутри").click()
            await page.locator("button[title='Nazarbayev University Main Building']").click()
            await page.wait_for_timeout(9000)
            await save("19_tour_walk_inside")
            await page.locator("button[title='Sports Complex of Nazarbayev University']").click()
            await page.wait_for_timeout(9000)
            await save("20_tour_walk_inside_sports")

        # ---- compare with the advisor's full answer
        async def advisor():
            t0 = time.time()
            while time.time() - t0 < 90:
                if not await page.locator("text=печатает").count():
                    break
                await page.wait_for_timeout(1000)
        await page_shot("21_compare", f"/compare?a={NU}&b={KBTU}", then=advisor, wait=6000)

        await page_shot("22_saved", "/saved")
        await page_shot("23_search_settings", "/settings/search")

        # ---- phone
        if want("24_mobile"):
            m = await new_ctx(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mp = await m.new_page()
            await mp.goto(f"{BASE}/u/{NU}", wait_until="domcontentloaded")
            await mp.wait_for_timeout(5000)
            await mp.screenshot(path=str(OUT / "24_mobile_profile.png"))
            await mp.goto(f"{BASE}/", wait_until="domcontentloaded")
            await mp.wait_for_timeout(9000)
            await mp.screenshot(path=str(OUT / "25_mobile_globe.png"))
            print("saved mobile", flush=True)
            await m.close()

        if want("26_share"):
            (OUT / "26_share_card.png").write_bytes(get(f"/api/og/{NU}.png"))
            print("saved 26_share_card", flush=True)
        await b.close()

asyncio.run(main())
