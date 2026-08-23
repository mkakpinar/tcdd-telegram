"""Bot entrypoint. Long-polls Telegram. Also runs the periodic alarm
checker every 30 min via PTB's JobQueue."""

from __future__ import annotations

import asyncio
import logging
import random
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationHandlerStop,
    ContextTypes,
    TypeHandler,
)

from .checker import run_once as checker_run_once
from .config import Settings, load_settings
from .handlers import access, alarm, ops, search, start
from .stations import StationCatalog
from .store import Store
from .tcdd import build_backend

# Default alarm-checker interval (minutes); override with CHECK_INTERVAL_MIN.


def _make_access_gate(settings: Settings):
    """Global gate (handler group -1): decides who may use the bot at all.

    Two sources, unioned: ALLOWED_CHAT_IDS from the environment (static, owned
    by whoever deploys) and a Redis set the admin manages from Telegram (see
    handlers/access.py). Both empty = open to everyone, as before.

    The Redis lookup happens per update so a grant takes effect on the next
    message instead of the next restart. If Redis is unreachable we fall back to
    the environment list rather than guessing: the bot neither throws its doors
    open nor locks out the people the deployment explicitly trusts.

    Stops propagation for everyone else, so commands, conversations, and button
    callbacks are all covered by this one handler."""
    static = settings.allowed_chat_ids
    admin = settings.admin_chat_id

    async def gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        cid = chat.id if chat else None
        if admin is not None and cid == admin:
            return
        if cid is not None and cid in static:
            return

        store = ctx.application.bot_data.get("store")
        try:
            if store is not None and cid is not None and await store.is_allowed(cid):
                return
            # Nobody configured anywhere means the bot is deliberately open.
            if not static and (store is None or not await store.any_allowed()):
                return
        except Exception:
            logging.exception("access check failed; falling back to ALLOWED_CHAT_IDS")
            if not static:
                return

        # The one thing a blocked user is allowed to do: ask for access. Without
        # this the button below would be inert, since pressing it arrives here
        # as another update from a chat that still isn't on the list.
        if update.callback_query and update.callback_query.data == access.REQUEST_CB:
            return

        logging.info("blocked unauthorized chat_id=%s", cid)
        if update.callback_query:
            await update.callback_query.answer()
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔️ Bu botu kullanma iznin yok.\n"
                f"Chat ID'in: {cid}\n"
                "Erişim istemek için aşağıdaki düğmeyi kullanabilirsin.",
                reply_markup=access.request_button(),
            )
        raise ApplicationHandlerStop

    return gate


async def _heartbeat_loop(app: Application) -> None:
    while True:
        try:
            await app.bot_data["store"].heartbeat()
        except Exception:
            logging.exception("heartbeat failed")
        await asyncio.sleep(60)


async def _check_alarms_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    app = ctx.application
    settings = app.bot_data["settings"]
    try:
        await checker_run_once(
            store=app.bot_data["store"],
            tcdd=app.bot_data["tcdd"],
            bot_token=settings.bot_token,
            tz=app.bot_data["tz"],
            admin_chat_id=settings.admin_chat_id,
        )
    except Exception:
        logging.exception("checker job failed")


async def _post_init(app: Application) -> None:
    settings = app.bot_data["settings"]
    app.bot_data["stations"] = await StationCatalog.load()
    app.bot_data["store"] = Store(settings.redis_url)
    app.bot_data["tcdd"] = build_backend(settings.tcdd_mode)
    app.bot_data["tz"] = ZoneInfo(settings.timezone)

    # Let the admin know the bot (re)started — so a crash/restart isn't silent.
    if settings.admin_chat_id:
        try:
            await app.bot.send_message(
                settings.admin_chat_id,
                f"✅ Bot başladı (tcdd={settings.tcdd_mode}, "
                f"kontrol {settings.check_interval_min} dk).",
            )
        except Exception:
            logging.exception("admin startup ping failed")

    app.bot_data["heartbeat_task"] = asyncio.create_task(_heartbeat_loop(app))
    # Schedule the alarm checker every CHECK_INTERVAL_MIN. The first run uses a
    # short random delay (30s–3m, capped under the interval) to stagger startup
    # and give TCDD natural jitter, without delaying a fast interval.
    interval_s = settings.check_interval_min * 60
    first_delay = random.uniform(30, min(180, interval_s))
    app.job_queue.run_repeating(
        _check_alarms_job,
        interval=interval_s,
        first=first_delay,
        name="check-alarms",
    )
    logging.info(
        "Bot ready (tcdd=%s); checker scheduled in %.0fs, then every %ds",
        settings.tcdd_mode, first_delay, interval_s,
    )


async def _post_shutdown(app: Application) -> None:
    task = app.bot_data.get("heartbeat_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    tcdd = app.bot_data.get("tcdd")
    if tcdd is not None and hasattr(tcdd, "aclose"):
        await tcdd.aclose()
    store = app.bot_data.get("store")
    if store is not None:
        await store.aclose()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs every request line at INFO, including the full Telegram API
    # URL — which embeds the bot token (api.telegram.org/bot<TOKEN>/...). Pin it
    # to WARNING so the token never lands in logs (docker logs, screenshots, etc.).
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = (
        Application.builder()
        .token(settings.bot_token)
        # PTB's default read/write timeout is 5s, too tight for the fra<->Telegram
        # hop — a slow API call would drop a conversation step (e.g. "Nereye?")
        # and make the flow look stuck. Give outgoing calls more headroom.
        .connect_timeout(10.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .pool_timeout(10.0)
        .connection_pool_size(8)
        .rate_limiter(AIORateLimiter())
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["settings"] = settings
    app.add_handler(TypeHandler(Update, _make_access_gate(settings)), group=-1)
    start.register(app)
    search.register(app)
    alarm.register(app)
    ops.register(app)
    access.register(app)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
