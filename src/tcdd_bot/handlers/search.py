"""Conversation builder for /search and /alarm — both ask for from/to/date/pax."""

from __future__ import annotations

import logging
from datetime import date
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .. import format as fmt
from ..tcdd import TcddBackend
from .common import date_picker_kb, passenger_picker_kb, station_picker_kb

log = logging.getLogger(__name__)

ASK_FROM, ASK_TO, ASK_DATE, ASK_PAX = range(4)


def build_trip_conversation(
    command: str,
    prefix: str,
    finish: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[int]],
    pre_check: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[bool]] | None = None,
) -> ConversationHandler:
    async def entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        if pre_check and not await pre_check(update, ctx):
            return ConversationHandler.END
        ctx.user_data.clear()
        ctx.user_data["mode"] = prefix
        await update.message.reply_text("Nereden? (örn: Söğütlüçeşme)")
        return ASK_FROM

    async def got_from_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        catalog = ctx.application.bot_data["stations"]
        matches = catalog.search(update.message.text, limit=5)
        if not matches:
            await update.message.reply_text("İstasyon bulunamadı, tekrar yaz.")
            return ASK_FROM
        await update.message.reply_text(
            "Hangisi?", reply_markup=station_picker_kb(matches, f"{prefix}_from")
        )
        return ASK_FROM

    async def picked_from(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        sid = int(q.data.split(":")[-1])
        catalog = ctx.application.bot_data["stations"]
        station = catalog.get(sid)
        ctx.user_data["from_id"] = sid
        ctx.user_data["from_name"] = station.name
        await q.edit_message_text(f"Nereden: *{station.name}*", parse_mode="Markdown")
        await q.message.reply_text("Nereye?")
        return ASK_TO

    async def got_to_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        catalog = ctx.application.bot_data["stations"]
        matches = catalog.search(update.message.text, limit=5)
        if not matches:
            await update.message.reply_text("İstasyon bulunamadı, tekrar yaz.")
            return ASK_TO
        await update.message.reply_text(
            "Hangisi?", reply_markup=station_picker_kb(matches, f"{prefix}_to")
        )
        return ASK_TO

    async def picked_to(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        sid = int(q.data.split(":")[-1])
        catalog = ctx.application.bot_data["stations"]
        station = catalog.get(sid)
        ctx.user_data["to_id"] = sid
        ctx.user_data["to_name"] = station.name
        await q.edit_message_text(f"Nereye: *{station.name}*", parse_mode="Markdown")
        await q.message.reply_text(
            "Hangi gün?", reply_markup=date_picker_kb(f"{prefix}_d")
        )
        return ASK_DATE

    async def picked_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        d = date.fromisoformat(q.data.split(":")[-1])
        ctx.user_data["date"] = d
        await q.edit_message_text(
            f"Tarih: *{d.strftime('%d.%m.%Y')}*", parse_mode="Markdown"
        )
        await q.message.reply_text(
            "Kaç yolcu?", reply_markup=passenger_picker_kb(f"{prefix}_p")
        )
        return ASK_PAX

    async def picked_pax(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        n = int(q.data.split(":")[-1])
        ctx.user_data["pax"] = n
        await q.edit_message_text(
            f"Yolcu: *{n}*", parse_mode="Markdown"
        )
        return await finish(update, ctx)

    async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text("İptal edildi.")
        return ConversationHandler.END

    return ConversationHandler(
        entry_points=[CommandHandler(command, entry)],
        states={
            ASK_FROM: [
                CallbackQueryHandler(picked_from, pattern=f"^{prefix}_from:station:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_from_text),
            ],
            ASK_TO: [
                CallbackQueryHandler(picked_to, pattern=f"^{prefix}_to:station:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_to_text),
            ],
            ASK_DATE: [
                CallbackQueryHandler(picked_date, pattern=f"^{prefix}_d:date:"),
            ],
            ASK_PAX: [
                CallbackQueryHandler(picked_pax, pattern=f"^{prefix}_p:pax:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )


async def _finish_search(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    ud = ctx.user_data
    tcdd: TcddBackend = ctx.application.bot_data["tcdd"]
    msg = update.callback_query.message
    await msg.reply_text("Arıyorum…")
    try:
        trains = await tcdd.search(
            ud["from_id"],
            ud["to_id"],
            ud["date"],
            ud["pax"],
            from_name=ud["from_name"],
            to_name=ud["to_name"],
        )
    except Exception as exc:
        log.exception("search failed")
        await msg.reply_text(f"TCDD arama hatası: {exc}")
        return ConversationHandler.END
    await msg.reply_markdown(
        fmt.render_search_results(
            ud["from_name"], ud["to_name"], ud["date"], ud["pax"], trains
        ),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def _rate_limit_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]
    ok = await store.check_search_rate(
        update.effective_chat.id, settings.search_rate_per_hour
    )
    if not ok:
        await update.message.reply_text(
            "Saatlik arama limitine ulaştın. Sonra tekrar dene."
        )
    return ok


def register(app) -> None:
    app.add_handler(
        build_trip_conversation("search", "s", _finish_search, _rate_limit_check)
    )
