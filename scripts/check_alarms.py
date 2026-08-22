"""Run the alarm checker once, from outside the bot process.

Useful for ad-hoc debugging or manual triggers
(e.g. `docker compose exec bot python scripts/check_alarms.py`).
The bot itself schedules this same logic every 30 min via PTB JobQueue — see
src/tcdd_bot/main.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tcdd_bot.checker import run_once  # noqa: E402
from tcdd_bot.config import load_settings  # noqa: E402
from tcdd_bot.main import configure_logging  # noqa: E402
from tcdd_bot.store import Store  # noqa: E402
from tcdd_bot.tcdd import build_backend  # noqa: E402


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    store = Store(settings.redis_url)
    tcdd = build_backend(settings.tcdd_mode)
    tz = ZoneInfo(settings.timezone)
    try:
        await run_once(
            store, tcdd, settings.bot_token, tz,
            admin_chat_id=settings.admin_chat_id,
        )
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())
