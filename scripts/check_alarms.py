"""Run the alarm checker once, from outside the bot process.

Useful for ad-hoc debugging or manual triggers (e.g. via `fly ssh console -C ...`).
The bot itself schedules this same logic every 30 min via PTB JobQueue — see
src/tcdd_bot/main.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tcdd_bot.checker import run_once  # noqa: E402
from tcdd_bot.config import load_settings  # noqa: E402
from tcdd_bot.store import Store  # noqa: E402
from tcdd_bot.tcdd import build_backend  # noqa: E402


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = Store(settings.redis_url)
    tcdd = build_backend(settings.tcdd_mode)
    tz = ZoneInfo(settings.timezone)
    try:
        await run_once(store, tcdd, settings.bot_token, tz)
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
