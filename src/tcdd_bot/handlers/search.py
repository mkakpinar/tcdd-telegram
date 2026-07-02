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
from .common import (
    STATIC_ROUTES,
    date_picker_kb,
    passenger_picker_kb,
    route_picker_kb,
    station_picker_kb,
    train_picker_kb,
    trainmode_kb,
)

log = logging.getLogger(__name__)

ASK_FROM, ASK_TO, ASK_DATE, ASK_TRAINMODE, ASK_TRAIN, ASK_PAX = range(6)


def build_trip_conversation(
    command: str,
    prefix: str,
    finish: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[int]],
    pick_train: bool = False,
) -> ConversationHandler:
    async def prompt_dates(message) -> None:
        await message.reply_text(
            "Hangi gün(ler)? Birden fazla seçebilirsin, sonra *Onayla*'ya bas.",
            parse_mode="Markdown",
            reply_markup=date_picker_kb(f"{prefix}_d"),
        )

    async def prompt_pax(message) -> None:
        await message.reply_text(
            "Kaç yolcu?", reply_markup=passenger_picker_kb(f"{prefix}_p")
        )

    async def entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        ctx.user_data.clear()
        ctx.user_data["mode"] = prefix
        await update.message.reply_text(
            "Nereden? (örn: Söğütlüçeşme)\nveya hazır bir rota seç:",
            reply_markup=route_picker_kb(prefix),
        )
        return ASK_FROM

    async def picked_route(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        idx = int(q.data.split(":")[-1])
        from_id, from_name, to_id, to_name, _ = STATIC_ROUTES[idx]
        ctx.user_data["from_id"] = from_id
        ctx.user_data["from_name"] = from_name
        ctx.user_data["to_id"] = to_id
        ctx.user_data["to_name"] = to_name
        await q.edit_message_text(
            f"Rota: *{from_name} → {to_name}*", parse_mode="Markdown"
        )
        await prompt_dates(q.message)
        return ASK_DATE

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
        await prompt_dates(q.message)
        return ASK_DATE

    async def picked_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        # Toggle a day in/out of the selection and re-render the keyboard.
        q = update.callback_query
        iso = q.data.split(":")[-1]
        selected: set[str] = ctx.user_data.setdefault("dates", set())
        selected.discard(iso) if iso in selected else selected.add(iso)
        await q.answer()
        await q.edit_message_reply_markup(
            reply_markup=date_picker_kb(f"{prefix}_d", selected)
        )
        return ASK_DATE

    async def dates_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        selected: set[str] = ctx.user_data.get("dates") or set()
        if not selected:
            await q.answer("En az bir gün seç.", show_alert=True)
            return ASK_DATE
        dates = sorted(date.fromisoformat(s) for s in selected)
        ctx.user_data["dates"] = dates  # set[str] -> sorted list[date]
        await q.answer()
        label = ", ".join(d.strftime("%d.%m.%Y") for d in dates)
        await q.edit_message_text(f"Tarih(ler): *{label}*", parse_mode="Markdown")
        if pick_train:
            await q.message.reply_text(
                "Tüm trenler mi, belirli tren(ler) mi?",
                reply_markup=trainmode_kb(prefix),
            )
            return ASK_TRAINMODE
        await prompt_pax(q.message)
        return ASK_PAX

    def _train_label(t) -> str:
        seats = f"{t.available_seats} koltuk" if t.available_seats else "dolu"
        return f"{t.train_no} · {t.departure_time.strftime('%H:%M')} · {seats}"

    async def chose_trainmode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        await q.answer()
        choice = q.data.split(":")[-1]
        ctx.user_data["target_trains"] = set()
        if choice == "all":
            await q.edit_message_text("Tüm trenler izlenecek.")
            await prompt_pax(q.message)
            return ASK_PAX
        # "pick": fetch the first selected day's full schedule (incl. sold-out).
        tcdd: TcddBackend = ctx.application.bot_data["tcdd"]
        ud = ctx.user_data
        day = ud["dates"][0]
        try:
            trains = await tcdd.search(
                ud["from_id"], ud["to_id"], day, 1,
                from_name=ud["from_name"], to_name=ud["to_name"],
                include_unavailable=True,
            )
        except Exception:
            log.exception("train picker fetch failed for %s", day)
            trains = []
        if not trains:
            await q.edit_message_text(
                "O gün için tren listesi alınamadı — tüm trenler izlenecek."
            )
            await prompt_pax(q.message)
            return ASK_PAX
        # Stash options for re-rendering on each toggle.
        ud["train_options"] = [(t.train_no, _train_label(t)) for t in trains]
        await q.edit_message_text(
            f"*{day.strftime('%d.%m.%Y')}* trenleri — izlemek istediklerini seç,"
            " sonra *Onayla*. (Seçilen tren no'ları tüm seçili günlerde izlenir.)",
            parse_mode="Markdown",
            reply_markup=train_picker_kb(prefix, ud["train_options"]),
        )
        return ASK_TRAIN

    async def picked_train(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        train_no = q.data.split(":")[-1]
        selected: set[str] = ctx.user_data.setdefault("target_trains", set())
        selected.discard(train_no) if train_no in selected else selected.add(train_no)
        await q.answer()
        await q.edit_message_reply_markup(
            reply_markup=train_picker_kb(
                prefix, ctx.user_data["train_options"], selected
            )
        )
        return ASK_TRAIN

    async def trains_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = update.callback_query
        selected: set[str] = ctx.user_data.get("target_trains") or set()
        if not selected:
            await q.answer("En az bir tren seç.", show_alert=True)
            return ASK_TRAIN
        await q.answer()
        await q.edit_message_text(
            f"🎯 Tren: *{', '.join(sorted(selected))}*", parse_mode="Markdown"
        )
        await prompt_pax(q.message)
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

    states = {
        ASK_FROM: [
            CallbackQueryHandler(picked_route, pattern=f"^{prefix}_route:"),
            CallbackQueryHandler(picked_from, pattern=f"^{prefix}_from:station:"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, got_from_text),
        ],
        ASK_TO: [
            CallbackQueryHandler(picked_to, pattern=f"^{prefix}_to:station:"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, got_to_text),
        ],
        ASK_DATE: [
            CallbackQueryHandler(dates_done, pattern=f"^{prefix}_d:datedone$"),
            CallbackQueryHandler(picked_date, pattern=f"^{prefix}_d:date:"),
        ],
        ASK_PAX: [
            CallbackQueryHandler(picked_pax, pattern=f"^{prefix}_p:pax:"),
        ],
    }
    if pick_train:
        states[ASK_TRAINMODE] = [
            CallbackQueryHandler(chose_trainmode, pattern=f"^{prefix}_tm:"),
        ]
        states[ASK_TRAIN] = [
            CallbackQueryHandler(trains_done, pattern=f"^{prefix}_t:traindone$"),
            CallbackQueryHandler(picked_train, pattern=f"^{prefix}_t:train:"),
        ]

    return ConversationHandler(
        entry_points=[CommandHandler(command, entry)],
        states=states,
        fallbacks=[CommandHandler("cancel", cancel)],
    )


async def _finish_search(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    ud = ctx.user_data
    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]
    tcdd: TcddBackend = ctx.application.bot_data["tcdd"]
    msg = update.callback_query.message
    # Charge the hourly rate limit here — at the actual TCDD query — so that
    # starting and abandoning /search does not consume quota.
    if not await store.check_search_rate(
        update.effective_chat.id, settings.search_rate_per_hour
    ):
        await msg.reply_text("Saatlik arama limitine ulaştın. Sonra tekrar dene.")
        return ConversationHandler.END
    await msg.reply_text("Arıyorum…")
    for d in ud["dates"]:
        try:
            trains = await tcdd.search(
                ud["from_id"],
                ud["to_id"],
                d,
                ud["pax"],
                from_name=ud["from_name"],
                to_name=ud["to_name"],
            )
        except Exception:
            log.exception("search failed for %s", d)
            await msg.reply_text(
                f"{d.strftime('%d.%m.%Y')}: arama sırasında bir sorun oluştu."
            )
            continue
        await msg.reply_markdown(
            fmt.render_search_results(
                ud["from_name"], ud["to_name"], d, ud["pax"], trains
            ),
            disable_web_page_preview=True,
        )
    return ConversationHandler.END


def register(app) -> None:
    app.add_handler(build_trip_conversation("search", "s", _finish_search))
