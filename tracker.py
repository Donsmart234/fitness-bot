"""State, schedule, logging and summaries (no Telegram code here, so it is easy to test)."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import content as C
from store import Store

TZ = ZoneInfo("Africa/Lagos")
DAY_START_H = 4            # a "plan day" runs 04:00 -> 04:00 local, so after-midnight logs count to the day before
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULTS = {"creatine": True, "wake": "07:00", "bed": "23:00", "workout_days": [0, 2, 4, 5], "workout_time": "17:00",
            "water_goal": 8, "paused": False}
GRACE_MIN = 25             # a reminder that was due more than this long ago (bot asleep / redeploy) is skipped


def now_local() -> datetime:
    return datetime.now(TZ)


def plan_day(dt: datetime) -> date:
    return (dt.astimezone(TZ) - timedelta(hours=DAY_START_H)).date()


def parse_hm(s: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm)?\s*", s.lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def fmt_hm(minutes: int) -> str:
    minutes %= 1440
    h, m = divmod(minutes, 60)
    ap = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {ap}" if m else f"{h12} {ap}"


def wake_bed(settings: dict) -> tuple[int, int]:
    wake = parse_hm(settings["wake"])
    bed = parse_hm(settings["bed"])
    if bed <= wake:
        bed += 1440
    return wake, bed


def sleep_hours(settings: dict) -> float:
    wake, bed = wake_bed(settings)
    return round((wake + 1440 - bed) / 60, 1)


def build_schedule(day: date, settings: dict) -> list[dict]:
    """All reminders for one plan day. minute = minutes after local midnight of `day` (may exceed 1440)."""
    wake, bed = wake_bed(settings)
    items: list[dict] = []

    def add(kind, slot, minute):
        items.append({"key": f"{day.isoformat()}|{kind}|{slot}", "kind": kind, "slot": slot, "minute": minute,
                      "day": day.isoformat()})

    meal_at = {"breakfast": wake + 30, "snack1": wake + 180, "lunch": wake + 360, "snack2": wake + 510,
               "dinner": min(wake + 690, bed - 210), "milk": bed - 60}
    for slot, m in meal_at.items():
        add("meal", slot, m)
    n, m = 1, wake + 15
    while m <= bed - 60:
        if all(abs(m - mm) >= 15 for mm in meal_at.values()):
            add("water", str(n), m)
            n += 1
        m += 105
    add("posture", "am", wake + 45)
    if settings.get("creatine"):
        add("creatine", "am", wake + 35)
    if day.weekday() in settings["workout_days"]:
        wt = parse_hm(settings["workout_time"])
        if wt < wake:
            wt += 1440
        add("workout", "main", wt)
    add("recap", "pm", bed - 80)
    add("winddown", "pm", bed - 30)
    add("sleep", "pm", bed)
    return sorted(items, key=lambda i: i["minute"])


def due_at(item: dict) -> datetime:
    d = date.fromisoformat(item["day"])
    return datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(minutes=item["minute"])


class Tracker:
    def __init__(self, store: Store):
        self.store = store
        self.state: dict = {}
        self.lock = asyncio.Lock()
        self._logs_cache: tuple[str, list] | None = None

    # ---- state -----------------------------------------------------------
    async def load(self):
        self.state = await self.store.load_state()
        self.state.setdefault("settings", {})
        for k, v in DEFAULTS.items():
            self.state["settings"].setdefault(k, v)
        self.state.setdefault("sent", {})
        self.state.setdefault("snoozes", [])
        self.state.setdefault("bests", {})

    async def save(self):
        keep = {(plan_day(now_local()) - timedelta(days=i)).isoformat() for i in range(3)}
        self.state["sent"] = {d: v for d, v in self.state["sent"].items() if d in keep}
        await self.store.save_state(self.state)

    @property
    def settings(self) -> dict:
        return self.state["settings"]

    @property
    def owner(self) -> int | None:
        return self.state.get("owner")

    async def claim_owner(self, user_id: int) -> bool:
        async with self.lock:
            if self.owner is None:
                self.state["owner"] = user_id
                self.state["start_date"] = plan_day(now_local()).isoformat()
                await self.save()
                return True
            return self.owner == user_id

    def week(self, day: date | None = None) -> int:
        day = day or plan_day(now_local())
        start = date.fromisoformat(self.state.get("start_date") or day.isoformat())
        return max(0, (day - start).days // 7)

    def day_index(self, day: date | None = None) -> int:
        return (day or plan_day(now_local())).toordinal()

    # ---- due reminders -----------------------------------------------------
    def due_items(self, now: datetime | None = None) -> list[dict]:
        now = now or now_local()
        today = plan_day(now)
        out = []
        for d in (today - timedelta(days=1), today):
            sent = set(self.state["sent"].get(d.isoformat(), []))
            for it in build_schedule(d, self.settings):
                t = due_at(it)
                if it["key"] not in sent and t <= now < t + timedelta(minutes=GRACE_MIN):
                    out.append(it)
        return out

    def mark_sent(self, key: str):
        day = key.split("|", 1)[0]
        lst = self.state["sent"].setdefault(day, [])
        if key not in lst:
            lst.append(key)

    def due_snoozes(self, now: datetime | None = None) -> list[dict]:
        now = now or now_local()
        due = [s for s in self.state["snoozes"] if datetime.fromisoformat(s["due"]) <= now]
        return due

    def add_snooze(self, key: str, minutes: int = 15) -> datetime:
        due = now_local() + timedelta(minutes=minutes)
        self.state["snoozes"] = [s for s in self.state["snoozes"] if s["key"] != key]
        self.state["snoozes"].append({"key": key, "due": due.isoformat()})
        return due

    def drop_snooze(self, key: str):
        self.state["snoozes"] = [s for s in self.state["snoozes"] if s["key"] != key]

    # ---- logs --------------------------------------------------------------
    async def log(self, type_: str, slot: str = "", text: str = "", value=None, day: date | None = None,
                  key: str = "") -> dict:
        now = now_local()
        entry = {"type": type_, "slot": slot, "text": text, "value": value, "key": key,
                 "day": (day or plan_day(now)).isoformat(), "ts": now.astimezone(timezone.utc).isoformat(),
                 "local": now.strftime("%H:%M")}
        await self.store.add_log(entry)
        self._logs_cache = None
        if type_ == "workout" and isinstance(value, dict):
            for ex, sets in value.items():
                if C.EXERCISES.get(ex, {}).get("kind") == "max" and sets:
                    try:
                        best = max(int(x) for x in sets)
                    except (TypeError, ValueError):
                        continue
                    if best > self.state["bests"].get(ex, 0):
                        self.state["bests"][ex] = best
            await self.save()
        return entry

    async def logs(self, days: int = 8) -> list[dict]:
        since_day = plan_day(now_local()) - timedelta(days=days)
        since = datetime(since_day.year, since_day.month, since_day.day, tzinfo=TZ).astimezone(timezone.utc).isoformat()
        if self._logs_cache and self._logs_cache[0] == since:
            return self._logs_cache[1]
        rows = await self.store.logs_since(since)
        self._logs_cache = (since, rows)
        return rows

    async def last_weight(self) -> tuple[float, str] | None:
        rows = await self.store.logs_since("2000-01-01")
        ws = [r for r in rows if r.get("type") == "weight"]
        if not ws:
            return None
        return float(ws[-1]["value"]), ws[-1]["day"]

    async def first_weight(self) -> float | None:
        rows = await self.store.logs_since("2000-01-01")
        ws = [r for r in rows if r.get("type") == "weight"]
        return float(ws[0]["value"]) if ws else None

    @staticmethod
    def day_stats(rows: list[dict], day: str) -> dict:
        r = [x for x in rows if x.get("day") == day]
        slots = {x["slot"] for x in r if x["type"] == "meal" and x.get("slot")}
        free_meals = [x for x in r if x["type"] == "meal" and not x.get("slot")]
        return {
            "water": sum(1 for x in r if x["type"] == "water"),
            "meal_slots": slots,
            "meals": len(slots) + len(free_meals),
            "meal_texts": [x.get("text") for x in r if x["type"] == "meal" and x.get("text")],
            "workout": any(x["type"] == "workout" for x in r),
            "posture": any(x["type"] == "posture" for x in r),
            "creatine": any(x["type"] == "creatine" for x in r),
            "slept": any(x["type"] == "sleep" for x in r),
        }

    def next_meal_slot(self, logged: set, now: datetime | None = None) -> str:
        now = now or now_local()
        day = plan_day(now)
        sched = {i["slot"]: due_at(i) for i in build_schedule(day, self.settings) if i["kind"] == "meal"}
        free = [s for s in C.MEALS if s not in logged]
        if not free:
            return ""
        return min(free, key=lambda s: abs((sched[s] - now).total_seconds()))

    # ---- summaries ---------------------------------------------------------
    async def today_text(self) -> str:
        day = plan_day(now_local())
        rows = await self.logs(1)
        s = self.day_stats(rows, day.isoformat())
        goal = self.settings["water_goal"]
        is_wday = day.weekday() in self.settings["workout_days"]
        meals = " ".join(("✅" if m in s["meal_slots"] else "▫️") + C.MEAL_EMOJI[m] for m in C.MEALS)
        lines = [f"📅 Today, {day.strftime('%a %d %b')}",
                 f"💧 Water: {s['water']}/{goal} glasses",
                 f"🍽 Meals: {s['meals']}/6  {meals}",
                 f"🏋️ Workout: " + ("done ✅" if s["workout"] else ("not yet" if is_wday else "rest day")),
                 f"🧍 Posture: " + ("done ✅" if s["posture"] else "not yet")]
        if self.settings.get("creatine"):
            lines.append("🥄 Creatine: " + ("taken ✅" if s["creatine"] else "not yet"))
        w = await self.last_weight()
        if w:
            lines.append(f"⚖️ Last weight: {w[0]:g} kg ({w[1]})")
        if s["meal_texts"]:
            lines.append("Ate: " + "; ".join(s["meal_texts"][-4:]))
        return "\n".join(lines)

    async def week_text(self) -> str:
        today = plan_day(now_local())
        rows = await self.logs(40)
        goal = self.settings["water_goal"]
        lines = ["📊 Last 7 days"]
        wk_done = wk_planned = 0
        start = date.fromisoformat(self.state.get("start_date") or today.isoformat())
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            if d < start:
                continue
            s = self.day_stats(rows, d.isoformat())
            planned = d.weekday() in self.settings["workout_days"]
            wk_planned += planned
            wk_done += s["workout"]
            wo = "🏋️✅" if s["workout"] else ("🏋️❌" if planned and i > 0 else ("🏋️…" if planned else "  😌"))
            lines.append(f"{d.strftime('%a')}  💧{s['water']:>2}  🍽{s['meals']}  {wo}" + ("  🧍" if s["posture"] else ""))
        lines.append(f"\nWorkouts: {wk_done}/{wk_planned}")
        lines.append(f"🔥 Food + water streak: {self.streak(rows, today, goal)} days")
        lines.append(f"😴 On-time bed streak: {self.streak(rows, today, goal, kind='sleep')} days")
        w = await self.last_weight()
        f = await self.first_weight()
        if w:
            diff = f"  ({w[0] - f:+.1f} kg since start)" if f is not None and f != w[0] else ""
            lines.append(f"⚖️ {w[0]:g} kg{diff}. Target 57-63 kg")
        else:
            lines.append("⚖️ No weight yet. Send /weight 52 next time you weigh yourself.")
        return "\n".join(lines)

    def streak(self, rows, today: date, goal: int, kind: str = "food") -> int:
        def ok(d):
            s = self.day_stats(rows, d.isoformat())
            if kind == "sleep":
                return s["slept"]
            return s["water"] >= goal and s["meals"] >= 5
        n = 0
        d = today if ok(today) else today - timedelta(days=1)
        while ok(d) and n < 400:
            n += 1
            d -= timedelta(days=1)
        return n

    async def recap_text(self) -> str:
        day = plan_day(now_local())
        rows = await self.logs(1)
        s = self.day_stats(rows, day.isoformat())
        goal = self.settings["water_goal"]
        good = []
        todo = []
        (good if s["water"] >= goal else todo).append(f"💧 {s['water']}/{goal} water")
        (good if s["meals"] >= 5 else todo).append(f"🍽 {s['meals']}/6 meals")
        if day.weekday() in self.settings["workout_days"]:
            (good if s["workout"] else todo).append("🏋️ workout")
        (good if s["posture"] else todo).append("🧍 posture")
        if self.settings.get("creatine"):
            (good if s["creatine"] else todo).append("🥄 creatine")
        msg = ["🌆 Evening recap"]
        if good:
            msg.append("Nice: " + ", ".join(good))
        if todo:
            msg.append("Still time for: " + ", ".join(todo))
        msg.append("Big day! 💪" if not todo else "Finish strong, then sleep on time 😴")
        return "\n".join(msg)
