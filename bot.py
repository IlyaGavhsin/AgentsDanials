import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = "8879552177:AAGa1KwVygs0vHZCurSGV1s7lwBMtZbgkQo"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    bot_username = (await context.bot.get_me()).username
    mention = f"@{bot_username}"

    if mention.lower() not in message.text.lower():
        return

    text = message.text.replace(mention, "").strip()
    total = len(text)
    without_spaces = len(text.replace(" ", ""))

    await message.reply_text(
        f"Символов с пробелами: {total}\n"
        f"Символов без пробелов: {without_spaces}"
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    import asyncio
    asyncio.run(app.run_polling())
