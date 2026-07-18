"""Ops commands: /status (anyone allowed) and /stop (admin-only).

Long-polling means we can only receive these while the machine is running, so
/stop can gracefully scale the machine to zero but there is no matching /start —
starting on demand is done via the GitHub Actions `tcdd active window` dispatch.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import httpx
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

log = logging.getLogger(__name__)


async def _stop_self_machine(token: str) -> tuple[bool, str]:
    """Stop this Fly machine via the Machines API.

    A plain process exit would be undone by `[[restart]] policy = "always"` in
    fly.toml; an explicit API stop transitions the machine to `stopped` and stays
    stopped. Fly injects FLY_APP_NAME / FLY_MACHINE_ID at runtime.
    """
    app_name = os.getenv("FLY_APP_NAME")
    machine_id = os.getenv("FLY_MACHINE_ID")
    if not app_name or not machine_id:
        return False, "FLY_APP_NAME/FLY_MACHINE_ID yok (Fly üzerinde değil?)"
    url = f"https://api.machines.dev/v1/apps/{app_name}/machines/{machine_id}/stop"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers={"Authorization": f"Bearer {token}"})
    except Exception as e:  # network error reaching the Machines API
        log.exception("machine stop request failed")
        return False, f"istek hatası: {e}"
    if resp.status_code >= 400:
        return False, f"Fly API {resp.status_code}: {resp.text[:200]}"
    return True, "ok"


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]

    region = os.getenv("FLY_REGION", "local")
    machine_id = os.getenv("FLY_MACHINE_ID", "local")
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
        f"• Bölge / makine: {region} · `{machine_id}`",
        f"• Kontrol aralığı: {settings.check_interval_min} dk",
        f"• Aktif alarm: {active}",
        f"• Son kontrol: {seen_txt}",
        "• Aktif pencere: 06:00–00:00 (İstanbul); gece makine kapalı.",
        "• Başlatmak için: GitHub Actions → *tcdd active window* → Run workflow → start.",
    ]
    await update.message.reply_markdown("\n".join(lines))


async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]
    chat_id = update.effective_chat.id

    if settings.admin_chat_id is None or chat_id != settings.admin_chat_id:
        await update.message.reply_text("⛔️ Bu komut sadece yönetici içindir.")
        return

    if not settings.fly_api_token:
        await update.message.reply_text(
            "⚙️ /stop yapılandırılmamış (FLY_API_TOKEN yok). Makine durdurulmadı."
        )
        return

    active = len(await store.active_alarm_ids())
    warn = f"⚠️ {active} aktif alarm var; makine dururken kontrol edilmez.\n" if active else ""
    await update.message.reply_text(
        f"{warn}🛑 Makineyi durduruyorum. Yeniden başlatmak için "
        "GitHub Actions → tcdd active window → Run workflow → start."
    )

    ok, detail = await _stop_self_machine(settings.fly_api_token)
    if not ok:
        await update.message.reply_text(f"❌ Durdurma başarısız: {detail}")


def register(app) -> None:
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
