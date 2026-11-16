"""Telegram Mini App: workout guide with demos, rep counter and rest timer. /app (UI), /api/* (initData-protected)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus

from aiohttp import web

import config
import content as C
from tracker import Tracker, now_local, plan_day

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
