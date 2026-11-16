"""Telegram Mini App: workout guide with demos, rep counter and rest timer. /app (UI), /api/* (initData-protected)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus

from aiohttp import web

import config
import content as C
from tracker import DAYS, Tracker, build_schedule, fmt_hm, now_local, plan_day, sleep_hours

TARGET = (57, 63)
LOG_TYPES = ("water", "meal", "creatine", "posture", "workout", "sleep")

log = logging.getLogger("webapp")
STATIC = Path(__file__).resolve().parent / "static"


def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400, now: float | None = None) -> dict | None:
    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    if len(data) != len(pairs):
        return None
    received = data.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        return None
    try:
        auth_date = int(data.get("auth_date", "0"))
        user = json.loads(data.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age or auth_date - now > 300:
        return None
    if not isinstance(user, dict) or not int(user.get("id", 0) or 0):
        return None
    return user


def sign_init_data(fields: dict, bot_token: str) -> str:
    from urllib.parse import urlencode
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def exercise_payload(ex_id: str, week: int, bests: dict) -> dict:
    ex = C.EXERCISES[ex_id]
    best = bests.get(ex_id)
    return {"id": ex_id, "name": ex["name"], "kind": ex["kind"], "sets": ex["sets"], "rest": ex["rest"],
            "tempo": ex.get("tempo", 3.0), "target": C.target(ex_id, week, best), "best": best or 0,
            "label": C.target_label(ex_id, week, best), "how": ex["how"], "easier": ex["easier"],
            "demo": f"/static/demos/{ex['demo']}.gif",
            "video": "https://www.youtube.com/results?search_query=" + quote_plus("how to do " + ex["video"])}


class MiniApp:
    def __init__(self, tracker: Tracker, bot=None):
        self.t = tracker
        self.bot = bot

    def register(self, app: web.Application):
        app.router.add_get("/app", self.index)
        app.router.add_static("/static", STATIC, show_index=False)
        app.router.add_post("/api/plan", self.plan)
        app.router.add_post("/api/session", self.session)
        for name in ("bootstrap", "today", "log", "undo", "weight", "progress", "settings"):
            app.router.add_post(f"/api/{name}", self._guard(getattr(self, "api_" + name)))

    def _guard(self, fn):
        async def handler(req):
            user, body = await self._auth(req)
            if not user:
                return web.json_response({"error": "This app only works for the bot's owner, opened from Telegram."},
                                         status=403)
            try:
                return await fn(user, body)
            except ValueError as e:
                return web.json_response({"error": str(e)}, status=400)
        return handler

    # ---- payloads -----------------------------------------------------------
    def plan_payload(self) -> dict:
        day = plan_day(now_local())
        week = self.t.week(day)
        bests = self.t.state.get("bests", {})
        return {"week": week + 1, "workout_day": day.weekday() in self.t.settings["workout_days"],
                "workout": [exercise_payload(e, week, bests) for e in C.WORKOUT],
                "posture": [exercise_payload(e, week, bests) for e in C.POSTURE]}

    def settings_payload(self) -> dict:
        s = self.t.settings
        return {"wake": s["wake"], "bed": s["bed"], "workout_days": s["workout_days"], "workout_time": s["workout_time"],
                "water_goal": s["water_goal"], "creatine": bool(s.get("creatine")), "paused": bool(s.get("paused")),
                "sleep_hours": sleep_hours(s)}

    async def today_payload(self, user: dict | None = None) -> dict:
        now = now_local()
        day = plan_day(now)
        rows = await self.t.logs(40)
        st = self.t.day_stats(rows, day.isoformat())
        today_rows = [r for r in rows if r.get("day") == day.isoformat()]
        sched = {i["slot"]: i["minute"] for i in build_schedule(day, self.t.settings) if i["kind"] == "meal"}
        idx = self.t.day_index(day)
        meals = []
        for slot in C.MEALS:
            texts = [r.get("text") or "" for r in today_rows if r["type"] == "meal" and r.get("slot") == slot]
            meals.append({"slot": slot, "name": C.MEAL_NAMES[slot], "emoji": C.MEAL_EMOJI[slot],
                          "time": fmt_hm(sched[slot]), "logged": slot in st["meal_slots"],
                          "text": next((x for x in reversed(texts) if x), ""), "idea": C.meal_idea(slot, idx),
                          "ideas": C.MEAL_IDEAS[slot]})
        extras = [r.get("text") or "" for r in today_rows if r["type"] == "meal" and not r.get("slot")]
        goal = self.t.settings["water_goal"]
        w = await self.t.last_weight()
        return {
            "name": (user or {}).get("first_name", ""),
            "date": day.isoformat(), "day_label": day.strftime("%A %d %B").replace(" 0", " "),
            "week": self.t.week(day) + 1, "workout_day": day.weekday() in self.t.settings["workout_days"],
            "water": {"n": st["water"], "goal": goal},
            "meals": meals, "meals_n": st["meals"], "extra_meals": extras,
            "workout": st["workout"], "posture": st["posture"], "creatine": st["creatine"], "slept": st["slept"],
            "creatine_on": bool(self.t.settings.get("creatine")), "paused": bool(self.t.settings.get("paused")),
            "next": None if self.t.settings.get("paused") else self.t.next_reminder(now),
            "streaks": {"food": self.t.streak(rows, day, goal), "sleep": self.t.streak(rows, day, goal, kind="sleep"),
                        "workout": self.t.workout_streak(rows, day)},
            "weight": {"kg": w[0], "day": w[1]} if w else None, "target": list(TARGET),
        }

    # ---- endpoints ------------------------------------------------------------
    async def api_bootstrap(self, user, _body):
        return web.json_response({"today": await self.today_payload(user), "plan": self.plan_payload(),
                                  "settings": self.settings_payload(), "guides": C.GUIDES})

    async def api_today(self, user, _body):
        return web.json_response(await self.today_payload(user))

    async def api_log(self, user, body):
        kind = body.get("type")
        if kind not in LOG_TYPES:
            raise ValueError("bad type")
        text = str(body.get("text") or "").strip()[:200]
        slot = ""
        if kind == "meal":
            slot = str(body.get("slot") or "")
            if slot and slot not in C.MEALS:
                raise ValueError("bad meal slot")
            if not slot:
                st = self.t.day_stats(await self.t.logs(1), plan_day(now_local()).isoformat())
                slot = self.t.next_meal_slot(st["meal_slots"])
        elif kind in ("workout", "posture") and not text:
            text = "marked done in app"
        await self.t.log(kind, slot=slot, text=text)
        return web.json_response({"ok": True, "slot": slot, "today": await self.today_payload(user)})

    async def api_undo(self, user, body):
        kind = body.get("type")
        if kind not in LOG_TYPES:
            raise ValueError("bad type")
        slot = body.get("slot")
        ok = await self.t.undo(kind, slot if kind == "meal" and slot is not None else None)
        return web.json_response({"ok": ok, "today": await self.today_payload(user)})

    async def api_weight(self, user, body):
        try:
            kg = round(float(str(body.get("kg", "")).replace(",", ".")), 1)
        except ValueError:
            raise ValueError("Enter your weight in kg, like 52.5")
        if not 25 <= kg <= 200:
            raise ValueError("That doesn't look like kg. Try something like 52.5")
        await self.t.log("weight", value=kg)
        return web.json_response({"ok": True, "progress": await self.progress_payload()})

    async def progress_payload(self) -> dict:
        today = plan_day(now_local())
        rows = await self.t.logs(31)
        heavy = await self.t.logs_of("weight", "workout", "posture")
        goal = self.t.settings["water_goal"]
        start = date.fromisoformat(self.t.state.get("start_date") or today.isoformat())
        by_day: dict[str, float] = {}
        for r in heavy:
            if r.get("type") == "weight":
                try:
                    by_day[r["day"]] = float(r["value"])
                except (TypeError, ValueError, KeyError):
                    pass
        history = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            s = self.t.day_stats(rows, d.isoformat())
            history.append({"day": d.isoformat(), "dow": DAYS[d.weekday()].title(), "dom": d.day,
                            "before_start": d < start, "water": s["water"], "meals": s["meals"],
                            "workout": s["workout"], "posture": s["posture"], "creatine": s["creatine"],
                            "planned": d.weekday() in self.t.settings["workout_days"]})
        bests = {}
        for r in heavy:
            if r.get("type") in ("workout", "posture") and isinstance(r.get("value"), dict):
                for ex, sets in r["value"].items():
                    if ex not in C.EXERCISES or not isinstance(sets, list):
                        continue
                    vals = [int(x) for x in sets if isinstance(x, (int, float))]
                    if vals and max(vals) > bests.get(ex, {}).get("best", 0):
                        bests[ex] = {"best": max(vals), "day": r.get("day")}
        for ex, v in self.t.state.get("bests", {}).items():
            if ex in C.EXERCISES and v > bests.get(ex, {}).get("best", 0):
                bests[ex] = {"best": v, "day": None}
        pbs = []
        for ex in C.WORKOUT + C.POSTURE:
            e = C.EXERCISES[ex]
            unit = "s" if e["kind"] == "time" else ("reps each leg" if e["kind"] == "reps_each" else "reps")
            b = bests.get(ex)
            pbs.append({"id": ex, "name": e["name"], "unit": unit, "best": b["best"] if b else 0,
                        "day": b["day"] if b else None, "demo": f"/static/demos/{e['demo']}.png"})
        return {"weights": [{"day": d, "kg": by_day[d]} for d in sorted(by_day)], "target": list(TARGET),
                "history": history, "water_goal": goal,
                "streaks": {"food": self.t.streak(rows, today, goal), "sleep": self.t.streak(rows, today, goal, kind="sleep"),
                            "workout": self.t.workout_streak(rows, today)},
                "bests": pbs}

    async def api_progress(self, _user, _body):
        return web.json_response(await self.progress_payload())

    async def api_settings(self, _user, body):
        patch = body.get("set")
        if isinstance(patch, dict) and patch:
            async with self.t.lock:
                self.t.apply_settings(patch)
                await self.t.save()
        return web.json_response({"ok": True, "settings": self.settings_payload()})

    async def index(self, _req):
        return web.FileResponse(STATIC / "app.html", headers={"Cache-Control": "no-cache"})

    async def _auth(self, req) -> tuple[dict | None, dict]:
        try:
            body = await req.json()
        except Exception:
            body = {}
        user = verify_init_data(body.get("initData", ""), config.TELEGRAM_BOT_TOKEN)
        if not user or self.t.owner is None or int(user["id"]) != self.t.owner:
            return None, body
        return user, body

    async def plan(self, req):
        user, _ = await self._auth(req)
        if not user:
            return web.json_response({"error": "This app only works for the bot's owner, opened from Telegram."},
                                     status=403)
        day = plan_day(now_local())
        week = self.t.week(day)
        bests = self.t.state.get("bests", {})
        rows = await self.t.logs(1)
        s = self.t.day_stats(rows, day.isoformat())
        return web.json_response({
            "week": week + 1,
            "workout_day": day.weekday() in self.t.settings["workout_days"],
            "done": {"workout": s["workout"], "posture": s["posture"]},
            "workout": [exercise_payload(e, week, bests) for e in C.WORKOUT],
            "posture": [exercise_payload(e, week, bests) for e in C.POSTURE],
        })

    async def session(self, req):
        user, body = await self._auth(req)
        if not user:
            return web.json_response({"error": "forbidden"}, status=403)
        kind = body.get("kind")
        if kind not in ("workout", "posture"):
            return web.json_response({"error": "bad kind"}, status=400)
        results = {}
        for ex, sets in (body.get("results") or {}).items():
            if ex in C.EXERCISES and isinstance(sets, list):
                results[ex] = [max(0, min(999, int(x))) for x in sets[:10] if isinstance(x, (int, float))]
        minutes = max(0, min(300, int(body.get("minutes") or 0)))
        old_bests = dict(self.t.state.get("bests", {}))
        await self.t.log(kind, value=results, text=f"{minutes} min via app")
        if self.bot:
            try:
                await self.bot.send_message(self.t.owner, summary_text(kind, results, minutes, old_bests))
            except Exception as e:
                log.warning("summary send failed: %s", e)
        return web.json_response({"ok": True})


def summary_text(kind: str, results: dict, minutes: int, old_bests: dict) -> str:
    title = "🏆 Workout done!" if kind == "workout" else "🧍 Posture done!"
    lines = [f"{title} {minutes} min"]
    prs = []
    for ex, sets in results.items():
        e = C.EXERCISES[ex]
        unit = " s" if e["kind"] == "time" else ""
        lines.append(f"• {e['name']}: {' / '.join(f'{x}{unit}' for x in sets) or '-'}")
        if e["kind"] == "max" and sets and max(sets) > old_bests.get(ex, 0) and old_bests.get(ex):
            prs.append(f"{e['name']} {max(sets)}")
    if prs:
        lines.append("🔥 New best: " + ", ".join(prs))
    lines.append("Now eat well and sleep on time, that's when muscle grows 💪")
    return "\n".join(lines)
