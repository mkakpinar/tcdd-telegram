from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from .. import format as fmt
from .search import build_trip_conversation

log = logging.getLogger(__name__)


async def _finish_alarm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ud = ctx.user_data
    store = ctx.application.bot_data["store"]
    settings = ctx.application.bot_data["settings"]
    chat_id = update.effective_chat.id
    active = await store.count_active_alarms(chat_id)
    if active >= settings.max_alarms_per_user:
        await update.callback_query.message.reply_text(
            f"En fazla {settings.max_alarms_per_user} aktif alarmın olabilir."
            " Önce birini sil (/alarms)."
        )
        return ConversationHandler.END
    aid = await store.create_alarm(
        chat_id=chat_id,
        from_id=ud["from_id"],
        to_id=ud["to_id"],
        from_name=ud["from_name"],
        to_name=ud["to_name"],
        travel_dates=ud["dates"],
        passengers=ud["pax"],
    )
    dates = ", ".join(d.strftime("%d.%m.%Y") for d in ud["dates"])
    await update.callback_query.message.reply_markdown(
        f"🔔 Alarm kuruldu! `{aid}`\n"
        f"{ud['from_name']} → {ud['to_name']}\n"
        f"{dates} · {ud['pax']} yolcu\n\n"
        "Yer çıkınca haber vereceğim. /alarms ile yönet."
    )
    return ConversationHandler.END


async def list_alarms(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = ctx.application.bot_data["store"]
    alarms = await store.list_user_alarms(update.effective_chat.id)
    text = fmt.render_alarm_list(alarms)
    rows = [
        [InlineKeyboardButton(fmt.alarm_button_label(a), callback_data=f"alm:del:{a.id}")]
        for a in alarms
    ]
    kb = InlineKeyboardMarkup(rows) if rows else None
    await update.message.reply_markdown(text, reply_markup=kb)


async def delete_alarm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Silindi")
    aid = q.data.split(":")[-1]
    store = ctx.application.bot_data["store"]
    await store.delete_alarm(aid)
    alarms = await store.list_user_alarms(update.effective_chat.id)
    text = fmt.render_alarm_list(alarms)
    rows = [
        [InlineKeyboardButton(fmt.alarm_button_label(a), callback_data=f"alm:del:{a.id}")]
        for a in alarms
    ]
    kb = InlineKeyboardMarkup(rows) if rows else None
    await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")


async def clear_alarms(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = ctx.application.bot_data["store"]
    n = await store.clear_user_alarms(update.effective_chat.id)
    await update.message.reply_text(f"{n} alarm silindi.")


async def pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = ctx.application.bot_data["store"]
    await store.set_paused(update.effective_chat.id, True)
    await update.message.reply_text(
        "⏸ Alarmların duraklatıldı. /resume ile tekrar başlatabilirsin."
    )


async def resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store = ctx.application.bot_data["store"]
    await store.set_paused(update.effective_chat.id, False)
    await update.message.reply_text("▶️ Alarmlar yeniden aktif.")


def register(app) -> None:
    app.add_handler(build_trip_conversation("alarm", "a", _finish_alarm))
    app.add_handler(CommandHandler("alarms", list_alarms))
    app.add_handler(CommandHandler("clear", clear_alarms))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CallbackQueryHandler(delete_alarm_cb, pattern=r"^alm:del:"))
