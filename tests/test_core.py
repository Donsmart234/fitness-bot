import asyncio, json, os, sys, tempfile, time
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:TEST"
import config; config.TELEGRAM_BOT_TOKEN = "123:TEST"
from store import Store
from tracker import Tracker, build_schedule, parse_hm, fmt_hm, DEFAULTS, TZ, plan_day, now_local
import content as C
from webapp import MiniApp, sign_init_data, summary_text
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

def test_schedule():
    s = dict(DEFAULTS)
    items = build_schedule(date(2026, 11, 16), s)  # Monday
    for i in items: print(fmt_hm(i["minute"]), i["kind"], i["slot"])
    kinds = [i["kind"] for i in items]
    assert kinds.count("meal") == 6 and "workout" in kinds and kinds.count("water") >= 7
    assert "workout" not in [i["kind"] for i in build_schedule(date(2026, 11, 17), s)]
    s2 = dict(s, bed="00:30", wake="08:30")
    it = build_schedule(date(2026, 11, 16), s2)
    assert max(i["minute"] for i in it) == 1440 + 30
    assert parse_hm("5pm") == 1020 and parse_hm("23:00") == 1380 and parse_hm("7") == 420 and parse_hm("12am") == 0

async def test_tracker():
    d = tempfile.mkdtemp()
    t = Tracker(Store(url="", key="", path=d + "/f.json"))
    await t.load()
    assert await t.claim_owner(42) and not await t.claim_owner(7) and await t.claim_owner(42)
    for _ in range(3): await t.log("water")
    await t.log("meal", slot="breakfast", text="pap")
    await t.log("weight", value=50.0)
    await t.log("workout", value={"pushups": [8, 10, 7], "squats": [15, 15, 15]})
    assert t.state["bests"]["pushups"] == 10
    print(await t.today_text()); print(await t.week_text()); print(await t.recap_text())
    # due items: pretend now = 7:16 today
    now = datetime.combine(plan_day(now_local()), datetime.min.time(), TZ) + timedelta(hours=7, minutes=16)
    due = t.due_items(now)
    print("due at 7:16:", [i["key"] for i in due])
    assert any(i["kind"] == "water" for i in due)
    for i in due: t.mark_sent(i["key"])
    assert t.due_items(now) == []
    # reload persists
    t2 = Tracker(Store(url="", key="", path=d + "/f.json")); await t2.load()
    assert t2.owner == 42 and t2.state["bests"]["pushups"] == 10
    return t

async def test_api(t):
    app = web.Application()
    class FakeBot:
        sent = []
        async def send_message(self, chat, text, **k): self.sent.append(text)
    fb = FakeBot()
    MiniApp(t, fb).register(app)
    async with TestClient(TestServer(app)) as c:
        bad = await c.post("/api/plan", json={"initData": "x"}); assert bad.status == 403
        init = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})}, "123:TEST")
        r = await c.post("/api/plan", json={"initData": init}); j = await r.json(); assert r.status == 200, j
        print("plan:", [(e["id"], e["label"]) for e in j["workout"]], [(e["id"], e["label"]) for e in j["posture"]])
        other = sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": 9})}, "123:TEST")
        assert (await c.post("/api/plan", json={"initData": other})).status == 403
        r = await c.post("/api/session", json={"initData": init, "kind": "workout", "minutes": 24,
                          "results": {"pushups": [12, 10, 9], "plank": [30, 30, 25]}})
        assert r.status == 200
        print(fb.sent[-1])
        h = await c.get("/app"); assert h.status == 200
        g = await c.get("/static/demos/pushups.gif"); assert g.status == 200
    assert t.state["bests"]["pushups"] == 12
    print(C.routine_text(5, {"pushups": 12}))

test_schedule()
t = asyncio.run(test_tracker())
asyncio.run(test_api(t))
print("ALL OK")
