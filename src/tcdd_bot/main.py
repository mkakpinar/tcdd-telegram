"""Bot entrypoint. Long-polls Telegram. Also runs the periodic alarm
checker every 30 min via PTB's JobQueue."""

from __future__ import annotations

import asyncio
import logging
import random
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    ContextTypes,
    TypeHandler,
)

from .checker import run_once as checker_run_once
from .config import Settings, load_settings
from .handlers import alarm, search, start
from .stations import StationCatalog
from .store import Store
from .tcdd import build_backend

CHECKER_INTERVAL_S = 30 * 60  # 30 minutes


def _make_access_gate(settings: Settings):
    """Global gate (handler group -1): if ALLOWED_CHAT_IDS is set, only those
    chat IDs (and ADMIN_CHAT_ID) may use the bot. Empty list = open to all.
    Stops propagation for everyone else so commands, conversations, and button
    callbacks are all blocked by this single handler."""
    allowed = settings.allowed_chat_ids
    admin = settings.admin_chat_id

    async def gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed:
            return
        chat = update.effective_chat
        cid = chat.id if chat else None
        if cid in allowed or (admin is not None and cid == admin):
            return
        logging.info("blocked unauthorized chat_id=%s", cid)
        if update.callback_query:
            await update.callback_query.answer()
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔️ Bu botu kullanma yetkin yok.\n"
                f"Chat ID'in: {cid}\n"
                "Erişim için yöneticiye bu numarayı ilet."
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
        )
    except Exception:
        logging.exception("checker job failed")


async def _post_init(app: Application) -> None:
    settings = app.bot_data["settings"]
    app.bot_data["stations"] = await StationCatalog.load()
    app.bot_data["store"] = Store(settings.redis_url)
    app.bot_data["tcdd"] = build_backend(settings.tcdd_mode)
    app.bot_data["tz"] = ZoneInfo(settings.timezone)

    asyncio.create_task(_heartbeat_loop(app))
    # Schedule the alarm checker: first run after a random 1–15 min delay,
    # then every 30 min thereafter. The random first delay spreads load
    # across bot restarts and gives TCDD natural jitter.
    first_delay = random.uniform(60, 15 * 60)
    app.job_queue.run_repeating(
        _check_alarms_job,
        interval=CHECKER_INTERVAL_S,
        first=first_delay,
        name="check-alarms",
    )
    logging.info(
        "Bot ready (tcdd=%s); checker scheduled in %.0fs, then every %ds",
        settings.tcdd_mode, first_delay, CHECKER_INTERVAL_S,
    )


async def _post_shutdown(app: Application) -> None:
    tcdd = app.bot_data.get("tcdd")
    if tcdd is not None and hasattr(tcdd, "aclose"):
        await tcdd.aclose()
    store = app.bot_data.get("store")
    if store is not None:
        await store.aclose()


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["settings"] = settings
    app.add_handler(TypeHandler(Update, _make_access_gate(settings)), group=-1)
    start.register(app)
    search.register(app)
    alarm.register(app)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
