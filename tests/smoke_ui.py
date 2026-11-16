"""Playwright smoke test of the Mini App with a stubbed Telegram.WebApp.
Run: python tests/smoke_ui.py [screenshot_dir] [light|dark]"""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config; config.TELEGRAM_BOT_TOKEN = "123:TEST"
from datetime import timedelta
from aiohttp import web
from playwright.async_api import async_playwright
from store import Store
from tracker import Tracker, plan_day, now_local
from webapp import MiniApp, sign_init_data

OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
SCHEME = sys.argv[2] if len(sys.argv) > 2 else "dark"
THEMES = {
    "dark": {"bg_color": "#000000", "secondary_bg_color": "#1c1c1d", "section_bg_color": "#2c2c2e", "text_color": "#ffffff",
             "hint_color": "#98989e", "section_separator_color": "#3d3d40", "button_color": "#3e88f7"},
    "light": {"bg_color": "#ffffff", "secondary_bg_color": "#efeff4", "section_bg_color": "#ffffff", "text_color": "#000000",
              "hint_color": "#8e8e93", "section_separator_color": "#c8c7cc", "button_color": "#007aff"},
}


async def seed(t: Tracker):
    await t.claim_owner(42)
    today = plan_day(now_local())
    t.state["start_date"] = (today - timedelta(days=12)).isoformat()
    for i, (kg, water, meals, wo) in enumerate([(50.2, 8, 6, True), (50.0, 6, 5, False), (50.6, 9, 6, True), (50.9, 7, 5, True)]):
        d = today - timedelta(days=10 - i * 3)
        await t.log("weight", value=kg, day=d)
        for _ in range(water): await t.log("water", day=d)
        for s in ["breakfast", "snack1", "lunch", "snack2", "dinner", "milk"][:meals]: await t.log("meal", slot=s, day=d)
        if wo: await t.log("workout", value={"pushups": [8 + i, 7, 6], "squats": [15, 15, 15], "plank": [30, 28, 25]}, day=d)
        await t.log("posture", value={"hang": [20, 18, 15], "wall": [60]}, day=d)
    for _ in range(3): await t.log("water")
    await t.log("meal", slot="breakfast", text="pap with milk and akara")


async def main():
    d = tempfile.mkdtemp()
    t = Tracker(Store(url="", key="", path=d + "/f.json")); await t.load(); await seed(t)
    hits = []
    app = web.Application()

    @web.middleware
    async def spy(req, handler):
        if req.path.startswith("/api/"): hits.append(req.path)
        return await handler(req)
    app.middlewares.append(spy)
    MiniApp(t, None).register(app)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8765); await site.start()
    init = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 42, "first_name": "Chris"})}, "123:TEST")
    theme = THEMES[SCHEME]
    css = ";".join(f"--tg-theme-{k.replace('_', '-')}:{v}" for k, v in theme.items())
    stub = """
    (function(){ const log = window.__tg = {main:[], back:[], haptic:0};
      const ev = {}; let backCb=null, mainCb=null;
      document.documentElement.setAttribute('style', %s);
      window.Telegram = {WebApp: {initData: %s, platform: 'ios', colorScheme: %s, themeParams: %s, version: '8.0',
        ready(){}, expand(){}, close(){ log.closed = true; }, setHeaderColor(){}, setBackgroundColor(){}, setBottomBarColor(){},
        onEvent(n, f){ ev[n]=f; }, openLink(u){ log.link = u; },
        showConfirm(m, cb){ log.confirm = m; cb(true); }, showPopup(p, cb){ cb('save'); },
        HapticFeedback: {impactOccurred(){ log.haptic++; }, notificationOccurred(){ log.haptic++; }, selectionChanged(){ log.haptic++; }},
        BackButton: {isVisible:false, show(){ this.isVisible=true; }, hide(){ this.isVisible=false; }, onClick(f){ backCb=f; }},
        MainButton: {isVisible:false, text:'', setParams(p){ Object.assign(this, p); this.isVisible = !!p.is_visible; }, show(){ this.isVisible=true; }, hide(){ this.isVisible=false; }, onClick(f){ mainCb=f; }},
      }};
      window.__back = () => backCb && backCb(); window.__main = () => mainCb && mainCb();
    })();""" % (json.dumps(css), json.dumps(init), json.dumps(SCHEME), json.dumps(theme))
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for vw, vh, tag in [(375, 667, "se"), (320, 568, "mini")]:
            ctx = await b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                      color_scheme=SCHEME)
            page = await ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            await page.route("https://telegram.org/**", lambda r: r.fulfill(status=200, content_type="text/javascript", body=stub))
            await page.goto("http://127.0.0.1:8765/app")
            await page.wait_for_selector("#wAdd")
            async def shot(name, full=True):
                await page.wait_for_timeout(450)
                await page.screenshot(path=f"{OUT}/{SCHEME}-{tag}-{name}-screen.png")
                if full:  # whole page, tab bar pinned to the end of the document instead of floating mid-image
                    h = await page.add_style_tag(content="body{position:relative}.tabbar{position:absolute!important}.toast{display:none}")
                    await page.screenshot(path=f"{OUT}/{SCHEME}-{tag}-{name}.png", full_page=True)
                    await h.evaluate("e => e.remove()")
            await shot("home")
            before = len([h for h in hits if h == "/api/log"])
            await page.click("#wAdd"); await page.wait_for_timeout(500)
            assert len([h for h in hits if h == "/api/log"]) == before + 1, "water tap did not hit /api/log"
            txt = await page.locator("#waterCard .big").inner_text(); assert txt.startswith("4"), txt
            await page.click("#wUndo"); await page.wait_for_timeout(500); assert "/api/undo" in hits
            # meal tile tap
            slot = await page.locator('.mtile:not(.done)').first.get_attribute("data-slot")
            await page.click(f'.mtile[data-slot="{slot}"]'); await page.wait_for_timeout(500)
            assert await page.locator(f'.mtile[data-slot="{slot}"].done').count() == 1
            # tabs
            await page.click('.tabbar [data-tab=workout]'); await page.wait_for_selector("#v-workout.on .ex")
            assert await page.evaluate("Telegram.WebApp.MainButton.isVisible")
            assert await page.evaluate("Telegram.WebApp.BackButton.isVisible")
            await shot("workout")
            await page.click('#wSeg [data-m=posture]'); await page.wait_for_timeout(200)
            assert "posture" in (await page.evaluate("Telegram.WebApp.MainButton.text")).lower()
            await page.click('#wSeg [data-m=workout]')
            # start session via MainButton, check timer engine runs, then quit (stub popup = save)
            await page.evaluate("__main()"); await page.wait_for_selector("#session", state="visible")
            assert not await page.evaluate("Telegram.WebApp.MainButton.isVisible")
            await page.click("#mainBtn"); await page.wait_for_timeout(300)  # Go now
            for _ in range(5): await page.click("#plus")
            await shot("session", full=False)
            await page.click("#mainBtn"); await page.wait_for_timeout(300)  # done set -> rest
            assert "Rest" in await page.locator("#small").inner_text()
            await page.evaluate("__back()"); await page.wait_for_selector("#finish", state="visible")
            await page.wait_for_function("document.getElementById('saveState').textContent.includes('Saved')")
            assert "/api/session" in hits
            await page.click("#closeBtn"); await page.wait_for_selector("#v-home.on")
            await page.click('.tabbar [data-tab=meals]'); await page.wait_for_selector("#v-meals.on .mslot")
            await page.fill("#qText", "bread and egg"); await page.click("#qLog"); await page.wait_for_timeout(500)
            assert await page.input_value("#qText") == ""
            await shot("meals")
            await page.click('.tabbar [data-tab=progress]'); await page.wait_for_selector("#wIn")
            await page.fill("#wIn", "51.4"); await page.click("#wLog"); await page.wait_for_timeout(600)
            assert "/api/weight" in hits
            assert "51.4" in await page.locator(".wnow .kg").inner_text()
            await shot("progress")
            await page.click('#rSeg [data-r="30"]'); await page.wait_for_timeout(200); await shot("progress30")
            await page.click('.tabbar [data-tab=settings]'); await page.wait_for_selector("#gPlus")
            await page.click("#gPlus"); await page.wait_for_timeout(1100)
            assert "/api/settings" in hits and t.settings["water_goal"] == 9, t.settings["water_goal"]
            await page.click("#gMinus"); await page.wait_for_timeout(1100)
            await shot("settings")
            await page.click('#setBody [data-guide=sleep]'); await page.wait_for_selector("#v-guide.on")
            await shot("guide")
            await page.evaluate("__back()"); await page.wait_for_selector("#v-settings.on")
            await page.evaluate("__back()"); await page.wait_for_selector("#v-home.on")
            assert not await page.evaluate("Telegram.WebApp.BackButton.isVisible")
            # horizontal overflow check on every tab
            for tb in ["home", "workout", "meals", "progress", "settings"]:
                await page.click(f'.tabbar [data-tab={tb}]'); await page.wait_for_timeout(300)
                ow = await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                assert ow <= 0, f"{tag} {tb} overflows by {ow}px"
            await ctx.close()
        await b.close()
    await runner.cleanup()
    assert not errors, errors
    print("UI SMOKE OK", SCHEME, "screens in", OUT, "api hits:", sorted(set(hits)))

asyncio.run(main())
