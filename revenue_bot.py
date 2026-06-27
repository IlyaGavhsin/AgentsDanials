"""Telegram-бот: считает выручку «чистыми» за текущий запуск из Google Таблиц.

Логика: в каждом листе таблица сама считает итог текущего запуска в ячейке
«Итого чистыми запуск новый». Бот читает эту ячейку из всех нужных листов
(публичные таблицы, CSV-экспорт) и складывает.
"""

import asyncio
import csv
import io
import logging
import os
import re

import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TOKEN = os.environ["TOKEN"]

# ID двух публичных таблиц
TABLE_1 = "1Hw4XWvo-66bTRuFsAVSIr1vtQ1ay4NMzGx5G4IZ-GH4"
TABLE_2 = "1jTBQoU2Jj2EH-4KEni6o15J8s3jbnmQE2gjP3RGCkcM"

# Нужные листы: (имя, id таблицы, gid листа)
SHEETS = [
    ("Вика", TABLE_1, "15087702"),
    ("Ксюша", TABLE_1, "519785801"),
    ("Геля", TABLE_1, "0"),
    ("Дима", TABLE_1, "289644111"),
    ("Артём", TABLE_2, "1694170026"),
    ("Лия", TABLE_2, "1029948835"),
    ("Яна", TABLE_2, "2116755515"),
]

# Метка ячейки с итогом текущего запуска
LABEL = "итого чистыми запуск новый"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_num(s: str):
    """Парсит число из ячейки: 'р.2 641 669,27', '3078366,385', '38500'."""
    s = s.strip().replace("р.", "").replace("₽", "").replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    # запятая — десятичный разделитель; точка может быть тысячным
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_launch_total(spreadsheet_id: str, gid: str):
    """Скачивает лист как CSV и возвращает значение ячейки «Итого чистыми запуск новый»."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    rows = list(csv.reader(io.StringIO(resp.text)))
    # берём первую (верхнюю) ячейку с меткой — это текущий запуск
    for row in rows:
        for j, cell in enumerate(row):
            if LABEL in cell.strip().lower():
                for k in range(j + 1, len(row)):
                    value = parse_num(row[k])
                    if value is not None:
                        return value
    return None


def collect_revenue():
    """Считает выручку по всем листам. Возвращает (список (имя, сумма|None), итог)."""
    results = []
    total = 0.0
    for name, sid, gid in SHEETS:
        try:
            value = fetch_launch_total(sid, gid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось получить лист %s: %s", name, exc)
            value = None
        if value is not None:
            total += value
        results.append((name, value))
    return results, total


def money(value: float) -> str:
    """Форматирует число с пробелами: 11797638 -> '11 797 638'."""
    return f"{value:,.0f}".replace(",", " ")


def build_report() -> str:
    results, total = collect_revenue()
    lines = ["💰 *Получили чистыми* (текущий запуск)\n"]
    for name, value in results:
        if value is None:
            lines.append(f"• {name} — ⚠️ ошибка чтения")
        else:
            lines.append(f"• {name} — {money(value)} ₽")
    lines.append("➖➖➖➖➖➖➖➖")
    lines.append(f"*ИТОГО: {money(total)} ₽* 🔥")
    return "\n".join(lines)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    bot_username = (await context.bot.get_me()).username
    mention = f"@{bot_username}"
    if mention.lower() not in message.text.lower():
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    report = await asyncio.to_thread(build_report)
    await message.reply_text(report, parse_mode="Markdown")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот выручки запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling()
