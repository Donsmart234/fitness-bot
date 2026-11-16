"""Entry point: aiohttp server (/health, /app, /api/*, /telegram webhook) + PTB application + reminder loop.
Webhook mode when PUBLIC_URL / RENDER_EXTERNAL_URL is set, else long polling."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import signal

from aiohttp import web
from telegram import Update
from telegram.ext import Application
from telegram.request import HTTPXRequest

import bot
import config
from store import Store
from tracker import Tracker
from webapp import MiniApp

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs URLs that contain the bot token
log = logging.getLogger("main")
WEBHOOK_PATH = "/telegram"


def secret_token() -> str:
    return hashlib.sha256(("fitwh:" + config.TELEGRAM_BOT_TOKEN).encode()).hexdigest()[:48]


async def run():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    store = Store()
    tracker = Tracker(store)
    for attempt in range(1, 6):
        try:
            await tracker.load()
            break
        except Exception as e:
            log.warning("state load failed (%s): %s", attempt, e)
            await asyncio.sleep(5 * attempt)
    else:
        raise SystemExit("cannot load state")
    log.info("store=%s owner=%s", store.backend, "set" if tracker.owner else "unclaimed")

    webhook = bool(config.PUBLIC_URL)
    req = HTTPXRequest(connect_timeout=20, read_timeout=30, write_timeout=30, pool_timeout=10)
    b = Application.builder().token(config.TELEGRAM_BOT_TOKEN).request(req).post_init(bot.post_init)
    if webhook:
        b = b.updater(None)
    app = b.build()
    app.bot_data["tracker"] = tracker
    bot.register(app)

    web_app = web.Application()
    health = lambda _r: web.Response(text="ok")
    web_app.router.add_get("/health", health)
    web_app.router.add_get("/", health)
    web_app.router.add_get("/whoami", lambda _r: web.json_response(
        {"username": app.bot.username if app.bot._bot_user else None, "owner": bool(tracker.owner)}))
    MiniApp(tracker, app.bot).register(web_app)
    sec = secret_token()

    async def tg_hook(request):
        if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), sec):
            return web.Response(status=403)
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400)
        await app.update_queue.put(Update.de_json(data, app.bot))
        return web.Response(text="ok")

    if webhook:
        web_app.router.add_post(WEBHOOK_PATH, tg_hook)
    runner = None
    port = config.PORT or (8080 if webhook else 0)
    if port:
        runner = web.AppRunner(web_app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        log.info("http on :%s", port)

    for attempt in range(1, 9):
        try:
            await app.initialize()
            break
        except Exception as e:
            log.warning("telegram init failed (%s): %s", attempt, type(e).__name__)
            await asyncio.sleep(min(60, 5 * attempt))
    else:
        raise SystemExit("cannot reach Telegram")
    await bot.post_init(app)
    await app.start()
    if webhook:
        await app.bot.set_webhook(config.PUBLIC_URL + WEBHOOK_PATH, secret_token=sec,
                                  allowed_updates=["message", "callback_query"])
        log.info("webhook mode")
    else:
        await app.bot.delete_webhook()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        log.info("polling mode")

    tasks = [asyncio.create_task(bot.reminder_loop(app)), asyncio.create_task(bot.keepalive_loop())]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    for t in tasks:
        t.cancel()
    if not webhook:
        await app.updater.stop()
    await app.stop()
    if runner:
        await runner.cleanup()
    await store.aclose()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
