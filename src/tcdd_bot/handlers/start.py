from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

HELP_TEXT = (
    "🚂 *TCDD Bilet Botu*\n\n"
    "/search — Sefer ara (Nereden, Nereye, Tarih, Yolcu sayısı)\n"
    "/alarm — Boş yer çıkınca haber veren alarm kur\n"
    "/alarms — Aktif alarmlarını listele\n"
    "/clear — Tüm alarmlarını sil\n"
    "/pause — Tüm alarmlarını duraklat\n"
    "/resume — Alarmlarını yeniden başlat\n"
    "/help — Bu mesaj\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    store = context.application.bot_data["store"]
    await store.upsert_user(update.effective_chat.id, user.username if user else None)
    await update.message.reply_markdown(HELP_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(HELP_TEXT)


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
