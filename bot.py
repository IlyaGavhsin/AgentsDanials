"""Telegram-бот с двумя командами:

  /count <текст>          — посчитать символы (с пробелами и без).
  /short <объём> <текст>  — логически сократить текст до объёма (символы или %)
                            с помощью DeepSeek.

Текст можно дать после команды ИЛИ ответить командой на сообщение с текстом.

Примеры:
  /count привет мир
  /short 500 длинный текст...      → ужмёт до ~500 символов
  /short 50% длинный текст...      → ужмёт примерно вдвое
  (ответом на сообщение) /short 500
"""

import asyncio
import logging
import os
import re

from telegram import BotCommand, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.environ["TOKEN"]
# Ключ DeepSeek нужен только для /short. Без него /count всё равно работает.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def count_symbols(text: str) -> str:
    total = len(text)
    without_spaces = len(re.sub(r"\s", "", text))
    return (
        f"Символов с пробелами: {total}\n"
        f"Символов без пробелов: {without_spaces}"
    )


def extract_target(s: str):
    """Достаёт объём сокращения из строки и возвращает (chars, percent, остаток_текста).

    Понимает «500», «до 500 символов», «50%», «до 30 %».
    """
    pct = re.search(r"(\d+)\s*%", s)
    if pct:
        leftover = s[: pct.start()] + s[pct.end():]
        return None, max(1, min(99, int(pct.group(1)))), _clean(leftover)
    num = re.search(r"(\d+)\s*(?:символ\w*|знак\w*|зн\b)?", s)
    if num and num.group(1):
        leftover = s[: num.start()] + s[num.end():]
        return int(num.group(1)), None, _clean(leftover)
    return None, None, _clean(s)


def _clean(s: str) -> str:
    s = re.sub(r"\b(до|примерно)\b", "", s, flags=re.IGNORECASE)
    return s.strip(" :\n\t")


def shorten_text(text: str, target_chars: int | None, target_percent: int | None) -> str:
    """Логически сокращает текст через DeepSeek до заданного объёма."""
    from openai import OpenAI

    if target_percent is not None:
        instruction = f"Сократи этот текст примерно до {target_percent}% от его исходной длины"
    else:
        instruction = f"Сократи этот текст примерно до {target_chars} символов"

    system = (
        "Ты — опытный редактор. Логически и связно сокращаешь русский текст, "
        "сохраняя его главный смысл, ключевые факты, числа и тон. "
        "Не выдумывай новое, не добавляй вступлений и пояснений. "
        "Верни ТОЛЬКО сокращённый текст — без кавычек и комментариев."
    )

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=8000,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"{instruction}.\n\nТекст:\n{text}"},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _reply_text(update: Update) -> str:
    reply = update.message.reply_to_message
    return reply.text if (reply and reply.text) else ""


async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        text = _reply_text(update)
    if not text:
        await update.message.reply_text(
            "Дай текст: `/count привет мир` или ответь командой /count на сообщение."
        )
        return
    await update.message.reply_text(count_symbols(text))


async def short_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    raw = " ".join(context.args).strip() if context.args else ""

    target_chars, target_percent, leftover = extract_target(raw)

    if target_chars is None and target_percent is None:
        await message.reply_text(
            "Укажи объём: `/short 500 текст` (символов) или `/short 50% текст`.\n"
            "Можно и ответом на сообщение: `/short 500`."
        )
        return

    # источник текста: ответ на сообщение или то, что осталось после объёма
    source = _reply_text(update) or leftover
    if not source:
        await message.reply_text(
            "Не вижу текста. Напиши его после объёма "
            "(`/short 500 твой текст`) или ответь командой на сообщение."
        )
        return

    if not DEEPSEEK_API_KEY:
        await message.reply_text(
            "Для /short нужен ключ DeepSeek. Добавь переменную "
            "DEEPSEEK_API_KEY в настройках бота."
        )
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    try:
        result = await asyncio.to_thread(
            shorten_text, source, target_chars, target_percent
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка сокращения")
        await message.reply_text(f"Не получилось сократить: {exc}")
        return

    await message.reply_text(
        f"{result}\n\n— было {len(source)} симв., стало {len(result)} симв."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Что я умею:\n\n"
        "📏 /count <текст> — посчитать символы (с пробелами и без).\n"
        "✂️ /short <объём> <текст> — сократить текст.\n"
        "   • объём в символах: `/short 500 текст`\n"
        "   • объём в процентах: `/short 50% текст`\n"
        "   • можно ответить командой на сообщение: `/short 500`"
    )


async def post_init(app):
    await app.bot.set_my_commands(
        [
            BotCommand("count", "Посчитать символы"),
            BotCommand("short", "Сократить текст (до N или N%)"),
            BotCommand("help", "Помощь"),
        ]
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("count", count_command))
    app.add_handler(CommandHandler("short", short_command))
    app.add_handler(CommandHandler(["help", "start"], help_command))
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling()
