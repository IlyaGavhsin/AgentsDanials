"""Telegram-бот с двумя функциями (реагирует на упоминание @бот):

1) Подсчёт символов — пишешь боту текст, он считает символы с пробелами и без.
2) Логическое сокращение текста до нужного объёма (в символах или процентах)
   с помощью Claude (Anthropic). Сокращаемый текст берётся из сообщения, на
   которое ты отвечаешь (reply), либо после двоеточия в самом сообщении.

Примеры:
   @бот сюда любой текст                  → посчитает символы
   (ответом на текст) @бот сократи до 500 → ужмёт до ~500 символов
   (ответом на текст) @бот сократи до 50% → ужмёт примерно вдвое
   @бот сократи до 300: длинный текст...  → ужмёт текст после двоеточия
"""

import asyncio
import logging
import os
import re

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TOKEN = os.environ["TOKEN"]
# Ключ Anthropic нужен только для функции сокращения. Без него считалка символов работает.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SHORTEN_WORDS = ("сократи", "сократить", "сокращ", "короче", "ужми", "сожми", "укороти")


def count_symbols(text: str) -> str:
    total = len(text)
    without_spaces = len(re.sub(r"\s", "", text))
    return (
        f"Символов с пробелами: {total}\n"
        f"Символов без пробелов: {without_spaces}"
    )


def shorten_text(text: str, target_chars: int | None, target_percent: int | None) -> str:
    """Логически сокращает текст через Claude до заданного объёма."""
    import anthropic

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

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": f"{instruction}.\n\nТекст:\n{text}"}],
    )
    return "".join(b.text for b in message.content if b.type == "text").strip()


def parse_target(instruction: str):
    """Достаёт цель сокращения. Возвращает (target_chars, target_percent) — одно из них."""
    pct = re.search(r"(\d+)\s*%", instruction)
    if pct:
        return None, max(1, min(99, int(pct.group(1))))
    # «до 500 символов» / «до 500 знаков» / просто «до 500»
    chars = re.search(r"(\d+)\s*(?:символ\w*|знак\w*|зн\b)", instruction.lower())
    if chars:
        return int(chars.group(1)), None
    num = re.search(r"\b(?:до|примерно)\s*(\d+)", instruction.lower()) or re.search(
        r"(\d+)", instruction
    )
    if num:
        return int(num.group(1)), None
    return None, None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    bot_username = (await context.bot.get_me()).username
    mention = f"@{bot_username}"
    if mention.lower() not in message.text.lower():
        return

    body = re.sub(re.escape(mention), "", message.text, flags=re.IGNORECASE).strip()
    lower = body.lower()

    wants_shorten = (
        any(w in lower for w in SHORTEN_WORDS)
        or re.search(r"\d+\s*%", body)
        or re.search(r"\d+\s*(?:символ|знак)", lower)
    )

    if not wants_shorten:
        # Функция 1 — подсчёт символов (поведение по умолчанию)
        await message.reply_text(count_symbols(body))
        return

    # Функция 2 — сокращение
    if not ANTHROPIC_API_KEY:
        await message.reply_text(
            "Для сокращения нужен ключ Anthropic. Добавь переменную "
            "ANTHROPIC_API_KEY в настройках бота."
        )
        return

    # Откуда берём текст: ответ на сообщение, либо часть после двоеточия
    if message.reply_to_message and message.reply_to_message.text:
        source = message.reply_to_message.text
        instruction = body
    elif ":" in body:
        instruction, source = body.split(":", 1)
        source = source.strip()
    else:
        await message.reply_text(
            "Чтобы сократить текст:\n"
            "• ответь этим тегом на сообщение с текстом и укажи объём "
            "(`сократи до 500` или `сократи до 50%`), или\n"
            "• напиши `сократи до 500: твой текст`"
        )
        return

    target_chars, target_percent = parse_target(instruction)
    if target_chars is None and target_percent is None:
        await message.reply_text(
            "Укажи, до какого объёма сокращать — например `до 500` (символов) "
            "или `до 50%`."
        )
        return

    if not source:
        await message.reply_text("Не вижу текста для сокращения.")
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


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling()
