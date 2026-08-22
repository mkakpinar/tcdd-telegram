"""Ops command: /status (anyone allowed).

Long-polling means we can only answer while the process is running, so there is
no in-bot stop/start — the container is managed from the host (`make down` /
`make up`) and `restart: always` brings it back after a crash or reboot.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

log = logging.getLogger(__name__)


def _host_label() -> str:
    """Where this process is running, for /status.

    HOST_LABEL is set in docker-compose.yml; falls back to the container/host
    name so a bare `python -m tcdd_bot.main` still says something useful.
    """
    return os.getenv("HOST_LABEL") or socket.gethostname()


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]

    active = len(await store.active_alarm_ids())
    degraded = await store.is_checker_degraded()
    seen = await store.last_seen()
    if seen is not None:
        secs = int((datetime.now(UTC) - seen).total_seconds())
        seen_txt = f"{secs} sn önce" if secs < 90 else f"{secs // 60} dk önce"
    else:
        seen_txt = "bilinmiyor"

    lines = [
        "📟 *Bot durumu*",
        f"• Durum: {'⚠️ sorunlu' if degraded else '✅ sağlıklı'}",
        f"• Sunucu: `{_host_label()}`",
        f"• Mod: {settings.tcdd_mode}",
        f"• Kontrol aralığı: {settings.check_interval_min} dk",
        f"• Aktif alarm: {active}",
        f"• Son kontrol: {seen_txt}",
    ]
    await update.message.reply_markdown("\n".join(lines))


def register(app) -> None:
    app.add_handler(CommandHandler("status", status_cmd))
