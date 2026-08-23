"""Access management: the admin grants or revokes access from Telegram, and
users who hit the gate can ask for it.

The runtime allow-list lives in Redis so changes take effect on the next
message rather than the next redeploy. `ALLOWED_CHAT_IDS` stays authoritative
for whatever the deployment hardcoded — those entries can't be revoked from
chat, which is what keeps the admin from locking themselves out.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

log = logging.getLogger(__name__)

# How long a rejected user must wait before asking again. Long enough that a
# "no" isn't re-litigated within the hour, short enough to survive a misclick.
REJECT_COOLDOWN_S = 24 * 3600

REQUEST_CB = "access:req"
APPROVE_CB = "access:ok:"
REJECT_CB = "access:no:"


def request_button() -> InlineKeyboardMarkup:
    """Offered to blocked users by the access gate."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔑 Erişim iste", callback_data=REQUEST_CB)]]
    )


def _describe(username: str | None, full_name: str) -> str:
    name = full_name or "(isim yok)"
    return f"{name} (@{username})" if username else f"{name} (kullanıcı adı yok)"


def _is_admin(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    admin = ctx.application.bot_data["settings"].admin_chat_id
    return admin is not None and chat_id == admin


async def _reply_not_admin(update: Update) -> None:
    msg = update.effective_message
    if msg is not None:
        await msg.reply_text("⛔️ Bu komut sadece yönetici içindir.")


def _parse_chat_id(args: list[str]) -> int | None:
    if len(args) != 1:
        return None
    raw = args[0].lstrip("+")
    if not raw.lstrip("-").isdigit():
        return None
    return int(raw)


# --- user side: asking for access ---


async def request_access_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The blocked user pressed "Erişim iste".

    Runs in the default handler group, so it is only reached for chats the gate
    let through — except that the gate deliberately lets this one callback pass.
    """
    q = update.callback_query
    store = ctx.application.bot_data["store"]
    settings = ctx.application.bot_data["settings"]
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        await q.answer()
        return

    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or ""
    created = await store.create_access_request(
        chat_id=chat.id,
        user_id=user.id,
        username=user.username,
        full_name=full_name,
    )
    if not created:
        await q.answer("Zaten bekleyen bir talebin var.", show_alert=True)
        return

    await q.answer()
    await q.edit_message_text("📨 Talebin yöneticiye iletildi. Yanıt gelince haber vereceğim.")

    if settings.admin_chat_id is None:
        log.warning("access request from %s but ADMIN_CHAT_ID is unset", chat.id)
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Onayla", callback_data=f"{APPROVE_CB}{chat.id}"),
        InlineKeyboardButton("❌ Reddet", callback_data=f"{REJECT_CB}{chat.id}"),
    ]])
    try:
        await ctx.bot.send_message(
            settings.admin_chat_id,
            "🔑 *Erişim talebi*\n"
            f"• {_describe(user.username, full_name)}\n"
            f"• Sohbet ID: `{chat.id}`\n"
            f"• Kullanıcı ID: `{user.id}`",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception:
        log.exception("could not notify admin about access request from %s", chat.id)


# --- admin side: deciding ---


async def decide_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    store = ctx.application.bot_data["store"]
    chat = update.effective_chat
    if chat is None or not _is_admin(ctx, chat.id):
        await q.answer("Bu karar sadece yöneticiye ait.", show_alert=True)
        return

    approve = q.data.startswith(APPROVE_CB)
    target = int(q.data.split(":")[-1])
    req = await store.get_access_request(target)

    if approve:
        await store.add_allowed(target)
        await store.clear_access_request(target)
        verdict = "✅ Onaylandı"
        note = "✅ Erişimin onaylandı. /start ile başlayabilirsin."
    else:
        await store.clear_access_request(target, cooldown_s=REJECT_COOLDOWN_S)
        verdict = "❌ Reddedildi"
        note = "❌ Erişim talebin reddedildi."

    await q.answer()
    who = _describe(req.username, req.full_name) if req else str(target)
    await q.edit_message_text(f"{verdict} — {who} (`{target}`)", parse_mode="Markdown")

    try:
        await ctx.bot.send_message(target, note)
    except Exception:
        # The user may have blocked the bot since asking; the decision stands.
        log.warning("could not notify %s about access decision", target)


# --- admin commands ---


async def allow_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or not _is_admin(ctx, chat.id):
        await _reply_not_admin(update)
        return
    target = _parse_chat_id(ctx.args)
    if target is None:
        await update.message.reply_text("Kullanım: /allow <chat_id>")
        return
    store = ctx.application.bot_data["store"]
    added = await store.add_allowed(target)
    await store.clear_access_request(target)
    await update.message.reply_text(
        f"✅ `{target}` eklendi." if added else f"ℹ️ `{target}` zaten listede.",
        parse_mode="Markdown",
    )


async def deny_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or not _is_admin(ctx, chat.id):
        await _reply_not_admin(update)
        return
    target = _parse_chat_id(ctx.args)
    if target is None:
        await update.message.reply_text("Kullanım: /deny <chat_id>")
        return

    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]

    if target in settings.allowed_chat_ids:
        await update.message.reply_text(
            f"⚠️ `{target}` `.env` içindeki ALLOWED_CHAT_IDS'de tanımlı; "
            "buradan kaldırılamaz. Sunucuda `.env`'i düzenleyip `make up` çalıştır.",
            parse_mode="Markdown",
        )
        return

    removed = await store.remove_allowed(target)
    msg = f"🚫 `{target}` çıkarıldı." if removed else f"ℹ️ `{target}` listede değildi."
    # Emptying the list re-opens the bot to everyone; say so rather than let it
    # be discovered later.
    if removed and not settings.allowed_chat_ids and not await store.any_allowed():
        msg += "\n\n⚠️ Liste boşaldı — bot artık *herkese açık*."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def allowed_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or not _is_admin(ctx, chat.id):
        await _reply_not_admin(update)
        return

    settings = ctx.application.bot_data["settings"]
    store = ctx.application.bot_data["store"]
    dynamic = await store.list_allowed()
    static = set(settings.allowed_chat_ids)

    if not static and not dynamic:
        await update.message.reply_text(
            "📋 Liste boş — bot *herkese açık*.\n/allow <chat_id> ile kısıtlayabilirsin.",
            parse_mode="Markdown",
        )
        return

    lines = ["📋 *İzinli sohbetler*"]
    for cid in sorted(static | dynamic):
        tag = " · `.env`" if cid in static else ""
        lines.append(f"• `{cid}`{tag}")
    if settings.admin_chat_id is not None:
        lines.append(f"\nYönetici (her zaman izinli): `{settings.admin_chat_id}`")
    lines.append("\n`.env` işaretli olanlar sunucudan yönetilir, /deny ile silinemez.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def requests_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or not _is_admin(ctx, chat.id):
        await _reply_not_admin(update)
        return

    store = ctx.application.bot_data["store"]
    pending = await store.list_access_requests()
    if not pending:
        await update.message.reply_text("📭 Bekleyen erişim talebi yok.")
        return

    for req in pending:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Onayla", callback_data=f"{APPROVE_CB}{req.chat_id}"),
            InlineKeyboardButton("❌ Reddet", callback_data=f"{REJECT_CB}{req.chat_id}"),
        ]])
        await update.message.reply_text(
            "🔑 *Erişim talebi*\n"
            f"• {_describe(req.username, req.full_name)}\n"
            f"• Sohbet ID: `{req.chat_id}`\n"
            f"• Kullanıcı ID: `{req.user_id}`",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


def register(app) -> None:
    app.add_handler(CommandHandler("allow", allow_cmd))
    app.add_handler(CommandHandler("deny", deny_cmd))
    app.add_handler(CommandHandler("allowed", allowed_cmd))
    app.add_handler(CommandHandler("requests", requests_cmd))
    app.add_handler(CallbackQueryHandler(request_access_cb, pattern=f"^{REQUEST_CB}$"))
    app.add_handler(
        CallbackQueryHandler(decide_cb, pattern=r"^access:(ok|no):-?\d+$")
    )
