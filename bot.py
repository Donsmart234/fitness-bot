"""Telegram handlers + the reminder loop."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Update,
                      WebAppInfo)
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

import config
import content as C
from tracker import (DAYS, Tracker, fmt_hm, now_local, parse_hm, plan_day, sleep_hours, build_schedule)

log = logging.getLogger("bot")

COMMANDS = [
    ("today", "Today's water, meals, workout"), ("week", "Weekly summary + streaks"),
    ("water", "Log a glass of water"), ("ate", "Log a meal: /ate rice and beans"),
    ("workout", "Open the workout (demos + timer)"), ("posture", "Posture session"), ("creatine", "Creatine: log it, guide, on/off"),
    ("weight", "Log weight: /weight 52.5"), ("meals", "Today's meal ideas"),
    ("sleep", "Set bed + wake: /sleep 23:00 07:00"), ("workoutdays", "Set days: /workoutdays mon wed fri sat"),
    ("workouttime", "Set workout time: /workouttime 5pm"), ("settings", "Your schedule"),
    ("pause", "Pause reminders"), ("resume", "Resume reminders"), ("help", "All commands"),
]


def tracker(ctx: ContextTypes.DEFAULT_TYPE) -> Tracker:
    return ctx.application.bot_data["tracker"]


def app_url(path: str = "") -> str | None:
    return f"{config.PUBLIC_URL}/app{path}" if config.PUBLIC_URL else None


def owner_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        t = tracker(ctx)
        user = update.effective_user
        if not user or (t.owner is not None and user.id != t.owner):
            return
        if t.owner is None:
            if update.message and update.message.text and update.message.text.startswith("/start"):
                return await fn(update, ctx)
            if update.message:
                await update.message.reply_text("Send /start to begin.")
            return
        return await fn(update, ctx)
    return wrapper


def buttons(key: str, kind: str) -> InlineKeyboardMarkup:
    done = {"water": "✅ Drank it", "meal": "✅ Ate it", "sleep": "✅ In bed", "winddown": "✅ OK"}.get(kind, "✅ Done")
    rows = []
    if kind in ("workout", "posture") and app_url():
        page = "?s=workout" if kind == "workout" else "?s=posture"
        rows.append([InlineKeyboardButton("▶️ Start with timer", web_app=WebAppInfo(app_url(page)))])
    rows.append([InlineKeyboardButton(done, callback_data=f"d|{key}"),
                 InlineKeyboardButton("⏰ 15 min", callback_data=f"s|{key}"),
                 InlineKeyboardButton("⏭ Skip", callback_data=f"k|{key}")])
    return InlineKeyboardMarkup(rows)


async def reminder_text(t: Tracker, item: dict) -> str:
    kind, slot = item["kind"], item["slot"]
    day = plan_day(now_local())
    if kind == "water":
        rows = await t.logs(1)
        n = t.day_stats(rows, item["day"])["water"]
        return f"💧 Water time! Drink a full glass.\n{n}/{t.settings['water_goal']} today"
    if kind == "meal":
        idea = C.meal_idea(slot, t.day_index())
        tail = {"milk": "Protein while you sleep = growth 🌙",
                "breakfast": "Big breakfast, big day 💪"}.get(slot, "Every meal builds muscle 💪")
        return f"{C.MEAL_EMOJI[slot]} {C.MEAL_NAMES[slot]} time!\nIdea: {idea}\n{tail}"
    if kind == "posture":
        return C.posture_text(t.week())
    if kind == "creatine":
        return C.CREATINE_REMINDER
    if kind == "workout":
        return "⏰ Workout time!\n" + C.routine_text(t.week(), t.state["bests"])
    if kind == "recap":
        return await t.recap_text()
    if kind == "winddown":
        return f"🌙 30 min to bed. Dim the lights, put screens down, maybe a cup of milk.\nYou grow in deep sleep 😴"
    if kind == "sleep":
        return (f"📵 Phone away, lights off. Wake at {fmt_hm(parse_hm(t.settings['wake']))} = "
                f"{sleep_hours(t.settings):g} h of sleep. Good night!")
    return "⏰ Reminder"


async def send_reminder(bot, t: Tracker, item: dict, snoozed: bool = False):
    text = await reminder_text(t, item)
    markup = None if item["kind"] == "recap" else buttons(item["key"], item["kind"])
    await bot.send_message(t.owner, text, reply_markup=markup)


async def reminder_loop(app: Application):
    t: Tracker = app.bot_data["tracker"]
    while True:
        try:
            if t.owner and not t.settings.get("paused"):
                for item in t.due_items():
                    async with t.lock:
                        t.mark_sent(item["key"])
                        await t.save()
                    try:
                        await send_reminder(app.bot, t, item)
                    except TelegramError as e:
                        log.warning("send failed: %s", e)
                for s in t.due_snoozes():
                    async with t.lock:
                        t.drop_snooze(s["key"])
                        await t.save()
                    day, kind, slot = s["key"].split("|")
                    item = {"key": s["key"], "kind": kind, "slot": slot, "day": day}
                    try:
                        await send_reminder(app.bot, t, item, snoozed=True)
                    except TelegramError as e:
                        log.warning("snooze send failed: %s", e)
        except Exception:
            log.exception("reminder loop error")
        await asyncio.sleep(30)


async def keepalive_loop():
    if not config.PUBLIC_URL:
        return
    import httpx
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            await asyncio.sleep(600)
            try:
                await c.get(config.PUBLIC_URL + "/health")
            except Exception:
                pass


# ---------------------------------------------------------------- commands
HELP = ("Here's what I do:\n"
        "💧 /water – log a glass\n🍽 /ate rice and beans – log a meal\n"
        "🏋️ /workout – demos + rep counter + rest timer\n🧍 /posture – bar hang + wall stand\n🥄 /creatine – log it · /creatine guide · /creatine on|off\n"
        "⚖️ /weight 52.5 – log your weight\n📅 /today · 📊 /week\n🍛 /meals – today's meal ideas\n"
        "😴 /sleep 23:00 07:00 – set bed and wake time\n📆 /workoutdays mon wed fri sat · /workouttime 5pm\n"
        "⚙️ /settings · ⏸ /pause · ▶️ /resume\n\n"
        "You can also just type: \"water\", \"ate yam and egg\", \"weight 53\".")


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = tracker(ctx)
    await t.claim_owner(update.effective_user.id)
    await set_menu(ctx.application)
    s = t.settings
    await update.message.reply_text(
        "💪 Let's build you up: taller posture, more muscle, good weight.\n\n"
        f"I'll remind you to drink water, eat 5 meals + milk at night, train on "
        f"{', '.join(DAYS[d].title() for d in s['workout_days'])}, do posture work daily, and sleep on time "
        f"({fmt_hm(parse_hm(s['bed']))} to {fmt_hm(parse_hm(s['wake']))}).\n\n"
        "Tap ✅ on each reminder to log it. Weigh yourself when you can and send /weight.\n\n" + HELP)


@owner_only
async def cmd_help(update: Update, ctx):
    await update.message.reply_text(HELP)


@owner_only
async def cmd_today(update: Update, ctx):
    await update.message.reply_text(await tracker(ctx).today_text())


@owner_only
async def cmd_week(update: Update, ctx):
    await update.message.reply_text(await tracker(ctx).week_text())


async def log_water(update: Update, t: Tracker):
    await t.log("water")
    n = t.day_stats(await t.logs(1), plan_day(now_local()).isoformat())["water"]
    goal = t.settings["water_goal"]
    extra = " Goal hit! 🎉" if n == goal else ""
    await update.message.reply_text(f"💧 Logged. {n}/{goal} today.{extra}")


async def log_meal(update: Update, t: Tracker, what: str):
    s = t.day_stats(await t.logs(1), plan_day(now_local()).isoformat())
    slot = t.next_meal_slot(s["meal_slots"])
    await t.log("meal", slot=slot, text=what.strip()[:200])
    label = C.MEAL_NAMES.get(slot, "Extra meal")
    await update.message.reply_text(f"{C.MEAL_EMOJI.get(slot, '🍽')} {label} logged: {what.strip()}. Nice 💪")


async def log_weight(update: Update, t: Tracker, kg: float):
    if not 25 <= kg <= 200:
        await update.message.reply_text("Hmm, that doesn't look like kg. Try /weight 52.5")
        return
    prev = await t.last_weight()
    await t.log("weight", value=kg)
    msg = f"⚖️ {kg:g} kg logged."
    if prev:
        msg += f" {kg - prev[0]:+.1f} kg since {prev[1]}."
    if kg < 57:
        msg += f" {57 - kg:.1f} kg to your 57-63 kg target. Keep eating! 🍛"
    elif kg <= 63:
        msg += " You're in your target range 🎯"
    await update.message.reply_text(msg)


@owner_only
async def cmd_water(update: Update, ctx):
    await log_water(update, tracker(ctx))


@owner_only
async def cmd_ate(update: Update, ctx):
    what = " ".join(ctx.args)
    if not what:
        await update.message.reply_text("What did you eat? e.g. /ate rice and beans with fish")
        return
    await log_meal(update, tracker(ctx), what)


@owner_only
async def cmd_weight(update: Update, ctx):
    try:
        kg = float(ctx.args[0].lower().replace("kg", "").replace(",", "."))
    except (IndexError, ValueError):
        await update.message.reply_text("Send it like: /weight 52.5")
        return
    await log_weight(update, tracker(ctx), kg)


@owner_only
async def cmd_workout(update: Update, ctx):
    t = tracker(ctx)
    kb = None
    if app_url():
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start workout", web_app=WebAppInfo(app_url("?s=workout")))],
                                   [InlineKeyboardButton("✅ Already did it", callback_data="w|done")]])
    await update.message.reply_text(C.routine_text(t.week(), t.state["bests"]), reply_markup=kb)


@owner_only
async def cmd_creatine(update: Update, ctx):
    t = tracker(ctx)
    arg = (ctx.args[0].lower() if ctx.args else "")
    if arg in ("on", "off"):
        async with t.lock:
            t.settings["creatine"] = arg == "on"
            await t.save()
        await update.message.reply_text("🥄 Creatine reminder " + ("ON, daily after breakfast." if arg == "on" else "OFF."))
        return
    if arg in ("guide", "how", "help"):
        await update.message.reply_text(C.CREATINE_GUIDE)
        return
    await t.log("creatine", text="taken")
    await update.message.reply_text("🥄 Creatine logged ✅ Drink an extra glass of water today.\n/creatine guide for how to take it.")


@owner_only
async def cmd_posture(update: Update, ctx):
    t = tracker(ctx)
    kb = None
    if app_url():
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Start posture", web_app=WebAppInfo(app_url("?s=posture")))],
                                   [InlineKeyboardButton("✅ Already did it", callback_data="w|posture")]])
    await update.message.reply_text(C.posture_text(t.week()), reply_markup=kb)


@owner_only
async def cmd_meals(update: Update, ctx):
    t = tracker(ctx)
    i = t.day_index()
    sched = {it["slot"]: it["minute"] for it in build_schedule(plan_day(now_local()), t.settings) if it["kind"] == "meal"}
    lines = ["🍛 Today's meal ideas"]
    for s in C.MEALS:
        lines.append(f"{C.MEAL_EMOJI[s]} {fmt_hm(sched[s])} {C.MEAL_NAMES[s]}: {C.meal_idea(s, i)}")
    lines.append("\nCheap, filling, and full of protein. Add extra groundnut, eggs or milk whenever you can.")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_settings(update: Update, ctx):
    s = tracker(ctx).settings
    await update.message.reply_text(
        f"⚙️ Wake {fmt_hm(parse_hm(s['wake']))} · Bed {fmt_hm(parse_hm(s['bed']))} ({sleep_hours(s):g} h sleep)\n"
        f"Workout: {' '.join(DAYS[d].title() for d in s['workout_days'])} at {fmt_hm(parse_hm(s['workout_time']))}\n"
        f"Water goal: {s['water_goal']} glasses · Reminders: {'paused ⏸' if s['paused'] else 'on ✅'}")


@owner_only
async def cmd_sleep(update: Update, ctx):
    t = tracker(ctx)
    if len(ctx.args) < 2 or parse_hm(ctx.args[0]) is None or parse_hm(ctx.args[1]) is None:
        await update.message.reply_text("Send bed time then wake time, e.g. /sleep 23:00 07:00 (or /sleep 10pm 6am)")
        return
    bed, wake = parse_hm(ctx.args[0]), parse_hm(ctx.args[1])
    hrs = ((wake - bed) % 1440) / 60
    if hrs < 4 or hrs > 12:
        await update.message.reply_text("That gives a strange amount of sleep. Try something like /sleep 23:00 07:00")
        return
    t.settings["bed"] = f"{bed // 60:02d}:{bed % 60:02d}"
    t.settings["wake"] = f"{wake // 60:02d}:{wake % 60:02d}"
    await t.save()
    note = "" if hrs >= 8 else "\nTip: at 19, 8+ hours helps you grow and build muscle. Try going to bed a bit earlier 😴"
    await update.message.reply_text(f"😴 Bed {fmt_hm(bed)}, wake {fmt_hm(wake)} = {hrs:g} h. "
                                    f"All reminders moved to fit.{note}")


@owner_only
async def cmd_workoutdays(update: Update, ctx):
    t = tracker(ctx)
    days = []
    for a in " ".join(ctx.args).lower().replace(",", " ").split():
        for i, d in enumerate(DAYS):
            if a.startswith(d):
                days.append(i)
    if not days:
        await update.message.reply_text("Send days like: /workoutdays mon wed fri sat")
        return
    t.settings["workout_days"] = sorted(set(days))
    await t.save()
    await update.message.reply_text("📆 Workout days: " + " ".join(DAYS[d].title() for d in t.settings["workout_days"]))


@owner_only
async def cmd_workouttime(update: Update, ctx):
    t = tracker(ctx)
    m = parse_hm(" ".join(ctx.args)) if ctx.args else None
    if m is None:
        await update.message.reply_text("Send a time like: /workouttime 5pm or /workouttime 17:30")
        return
    t.settings["workout_time"] = f"{m // 60:02d}:{m % 60:02d}"
    await t.save()
    await update.message.reply_text(f"⏰ Workout reminder at {fmt_hm(m)}")


@owner_only
async def cmd_pause(update: Update, ctx):
    t = tracker(ctx)
    t.settings["paused"] = True
    await t.save()
    await update.message.reply_text("⏸ Reminders paused. Send /resume to turn them back on.")


@owner_only
async def cmd_resume(update: Update, ctx):
    t = tracker(ctx)
    t.settings["paused"] = False
    await t.save()
    await update.message.reply_text("▶️ Reminders back on. Let's go!")


WATER_RE = re.compile(r"^(i\s+)?((drank|drink|had|took)\s+)?(a\s+|one\s+|1\s+)?(glass|cup|bottle)?\s*(of\s+)?water\b|^💧", re.I)
ATE_RE = re.compile(r"^(i\s+)?(just\s+)?(ate|eat|eaten|had)\s+(?P<what>.+)", re.I)
WEIGHT_RE = re.compile(r"^(my\s+)?(weight|weigh|i\s+weigh)\s*(is)?\s*(?P<kg>\d{2,3}(?:[.,]\d)?)\s*(kg)?$|^(?P<kg2>\d{2,3}(?:[.,]\d)?)\s*kg$", re.I)
WORKOUT_RE = re.compile(r"^(workout|training|exercise)\s+(done|finished)|^(done|finished)\s+(my\s+)?(workout|training)", re.I)


@owner_only
async def on_text(update: Update, ctx):
    t = tracker(ctx)
    text = (update.message.text or "").strip()
    if WATER_RE.search(text):
        return await log_water(update, t)
    m = WEIGHT_RE.search(text)
    if m:
        return await log_weight(update, t, float((m.group("kg") or m.group("kg2")).replace(",", ".")))
    if re.search(r"\bcreatine\b", text, re.I) and not re.search(r"\b(how|guide|what)\b", text, re.I):
        await t.log("creatine", text="logged by message")
        return await update.message.reply_text("🥄 Creatine logged ✅ Drink an extra glass of water today.")
    if re.search(r"\bcreatine\b", text, re.I):
        return await update.message.reply_text(C.CREATINE_GUIDE)
    if WORKOUT_RE.search(text):
        await t.log("workout", text="logged by message")
        return await update.message.reply_text("🏋️ Workout logged! Proud of you 💪")
    m = ATE_RE.search(text)
    if m:
        return await log_meal(update, t, m.group("what"))
    await update.message.reply_text("I didn't catch that. Try \"water\", \"ate rice and beans\", \"weight 53\", or /help.")


# ---------------------------------------------------------------- buttons
@owner_only
async def on_button(update: Update, ctx):
    q = update.callback_query
    t = tracker(ctx)
    data = q.data or ""
    stamp = now_local().strftime("%H:%M")
    if data.startswith("w|"):
        kind = "workout" if data == "w|done" else "posture"
        await t.log(kind, text="marked done")
        await q.answer("Logged 💪")
        await q.edit_message_text((q.message.text or "") + f"\n\n✅ Done at {stamp}")
        return
    action, _, key = data.partition("|")
    try:
        day, kind, slot = key.split("|")
    except ValueError:
        await q.answer()
        return
    base = q.message.text or ""
    if action == "d":
        if kind == "water":
            await t.log("water", key=key)
        elif kind == "meal":
            await t.log("meal", slot=slot, key=key)
        elif kind in ("workout", "posture"):
            await t.log(kind, text="marked done", key=key)
        elif kind == "sleep":
            await t.log("sleep", key=key)
        elif kind == "creatine":
            await t.log("creatine", key=key)
        t.drop_snooze(key)
        await q.answer("Logged ✅")
        foot = f"✅ Done at {stamp}"
        if kind == "water":
            n = t.day_stats(await t.logs(1), plan_day(now_local()).isoformat())["water"]
            foot += f" · {n}/{t.settings['water_goal']} today"
        await q.edit_message_text(base + "\n\n" + foot)
    elif action == "s":
        due = t.add_snooze(key)
        async with t.lock:
            await t.save()
        await q.answer("Snoozed 15 min")
        await q.edit_message_text(base + f"\n\n⏰ Snoozed to {due.strftime('%H:%M')}")
    elif action == "k":
        t.drop_snooze(key)
        await t.save()
        await q.answer("Skipped")
        await q.edit_message_text(base + "\n\n⏭ Skipped")
    else:
        await q.answer()


async def set_menu(app: Application):
    t: Tracker = app.bot_data["tracker"]
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        if app_url():
            mb = MenuButtonWebApp("Workout", WebAppInfo(app_url()))
            await app.bot.set_chat_menu_button(chat_id=t.owner, menu_button=mb) if t.owner else None
    except TelegramError as e:
        log.warning("menu setup failed: %s", e)


async def post_init(app: Application):
    await set_menu(app)


def register(app: Application):
    for name, fn in [("start", cmd_start), ("help", cmd_help), ("today", cmd_today), ("week", cmd_week),
                     ("water", cmd_water), ("ate", cmd_ate), ("weight", cmd_weight), ("workout", cmd_workout),
                     ("posture", cmd_posture), ("creatine", cmd_creatine), ("meals", cmd_meals), ("settings", cmd_settings),
                     ("sleep", cmd_sleep), ("workoutdays", cmd_workoutdays), ("workouttime", cmd_workouttime),
                     ("pause", cmd_pause), ("resume", cmd_resume)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
