"""Bot entrypoint. Long-polls Telegram."""

from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application

from .config import load_settings
from .handlers import alarm, search, start
from .stations import StationCatalog
from .store import Store
from .tcdd import build_backend


async def _post_init(app: Application) -> None:
    settings = app.bot_data["settings"]
    app.bot_data["stations"] = await StationCatalog.load()
    app.bot_data["store"] = Store(settings.upstash_url, settings.upstash_token)
    app.bot_data["tcdd"] = build_backend(settings.tcdd_mode)
    logging.info("Bot ready (tcdd=%s)", settings.tcdd_mode)


async def _heartbeat(app: Application) -> None:
    while True:
        try:
            await app.bot_data["store"].heartbeat()
        except Exception:
            logging.exception("heartbeat failed")
        await asyncio.sleep(60)


async def _post_start(app: Application) -> None:
    app.create_task(_heartbeat(app))


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
        .post_start(_post_start)
        .build()
    )
    app.bot_data["settings"] = settings
    start.register(app)
    search.register(app)
    alarm.register(app)
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
