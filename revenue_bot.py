"""Telegram-бот отчёта по продажам.

Источники (публичные Google Таблицы, бот их только читает):
  • Таблица «Артём/Лия/Яна» (+ листы ОТЧЕТ / ежедневные метрики)
  • Таблица «Вика/Ксюша/Геля/Дима» (+ листы ОТЧЕТ / ежедневные метрики)

Что считает:
  • Выручка чистыми за вчера и за сегодня — по столбцу «Итого чистые» листов менеджеров,
    по дате оплаты (возвраты исключаются).
  • Итог с начала запуска — по ячейке «Итого чистыми запуск новый» каждого листа.
  • Воронка за вчера (Лиды/Пуши/Продажи/Игнор/Отказы/Потенциалы) — с листов «ежедневные метрики».

Отправляет отчёт каждый день в 12:00 МСК (в чат REPORT_CHAT_ID) и по любому упоминанию бота.
"""

import asyncio
import csv
import io
import logging
import os
import random
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TOKEN = os.environ["TOKEN"]
# Чат, куда слать ежедневный отчёт в 12:00 МСК (id группы). Если не задан — шлём только по тегу.
REPORT_CHAT_ID = os.environ.get("REPORT_CHAT_ID")

MSK = ZoneInfo("Europe/Moscow")

# ID двух таблиц
TABLE_ALY = "14T_MLHMGlhWYVGyQ8xYiR8Ph2cp5-8Y7w3dNc8UgRfU"  # Артём, Лия, Яна
TABLE_VKGD = "1lmKbo27PsSk0GpIt-jl-ck7XWT2SsyCDswyNyagL3vU"  # Вика, Ксюша, Геля, Дима

# Листы менеджеров: (имя, id таблицы, gid)
MANAGERS = [
    ("Вика", TABLE_VKGD, "15087702"),
    ("Ксюша", TABLE_VKGD, "519785801"),
    ("Геля", TABLE_VKGD, "0"),
    ("Дима", TABLE_VKGD, "289644111"),
    ("Артём", TABLE_ALY, "1694170026"),
    ("Лия", TABLE_ALY, "1029948835"),
    ("Яна", TABLE_ALY, "2116755515"),
]

# Листы «ежедневные метрики» (агрегат по таблице): (id таблицы, gid)
DAILY_METRICS = [
    (TABLE_ALY, "315456011"),
    (TABLE_VKGD, "2116763388"),
]

LAUNCH_LABEL = "итого чистыми запуск новый"

# Разные приветствия — выбирается случайно каждый раз
GREETINGS = [
    "Доброе утро, любимая команда! ☀️ Погнали покорять цифры)",
    "Привет, команда мечты! 🚀 Свежий отчётик подъехал)",
    "Здарова, легенды продаж! 🔥 Смотрим, что нанабивали)",
    "Доброго дня, бойцы! 💪 Кто сегодня лучший — сейчас узнаем)",
    "Команда, салют! 🎉 Время сводки с фронта продаж)",
    "Привет, акулы продаж! 🦈 Заглотим немного цифр)",
    "Хей-хей, дримтим! 🌟 Отчёт горяченький, налетай)",
    "Доброе утро, чемпионы! 🏆 Цифры не врут — поехали)",
    "Йо, команда! 😎 Сводка дня готова, чекаем)",
    "Привет, ракеты! 🚀 Топливо залито, смотрим показатели)",
    "Здравствуй, непобедимая! ⚡️ Лови отчётик)",
    "Доброго, золотая команда! 🥇 Цифры дня на блюдечке)",
    "Командааа, подъём! ☕️ Кофе в руку, отчёт на экран)",
    "Привет, машина продаж! 🤖 Свежие данные подвезли)",
    "Хорошего дня, красавчики! 😏 Глянем на результаты)",
    "Салют, команда-огонь! 🔥 Сводка прибыла)",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\s*(\d{1,2})[.,](\d{1,2})\s*$")


def parse_num(s: str):
    """Парсит число из ячейки: 'р.2 641 669,27', '3078366,385', '38500'."""
    s = s.strip().replace("р.", "").replace("₽", "").replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_rows(table_id: str, gid: str):
    url = (
        f"https://docs.google.com/spreadsheets/d/{table_id}"
        f"/export?format=csv&gid={gid}"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return list(csv.reader(io.StringIO(resp.text)))


def launch_total(rows) -> float:
    """Значение ячейки «Итого чистыми запуск новый» (итог текущего запуска)."""
    for row in rows:
        for j, cell in enumerate(row):
            if LAUNCH_LABEL in cell.strip().lower():
                for k in range(j + 1, len(row)):
                    value = parse_num(row[k])
                    if value is not None:
                        return value
    return 0.0


def day_revenue(rows, day: int, month: int) -> float:
    """Сумма «Итого чистые» по строкам с датой day.month (возвраты исключены)."""
    total = 0.0
    for row in rows:
        if len(row) < 7:
            continue
        if any("озврат" in c.lower() for c in row):
            continue
        m = DATE_RE.match(row[0])
        if not m:
            continue
        if int(m.group(1)) == day and int(m.group(2)) == month:
            value = parse_num(row[6])
            if value:
                total += value
    return total


def metrics_for(metrics_sheets, day: int, month: int):
    """Воронка за день, суммой по обеим таблицам.

    Колонки листа: 1 новых, 2 старых, 3 пуши, 4 продажа, 5 игнор, 6 отказ, 7 потенц.
    Возвращает dict с ключами leads/push/sales/ignore/refusal/potential.
    """
    agg = [0.0] * 7
    target = f"{day:02d}.{month:02d}"
    target_alt = f"{day}.{month}"
    for rows in metrics_sheets:
        for row in rows:
            if not row or not row[0].strip():
                continue
            d = row[0].strip()
            if d not in (target, target_alt):
                continue
            for i in range(7):
                if i + 1 < len(row):
                    v = parse_num(row[i + 1])
                    if v:
                        agg[i] += v
    return {
        "leads": agg[0] + agg[1],
        "push": agg[2],
        "sales": agg[3],
        "ignore": agg[4],
        "refusal": agg[5],
        "potential": agg[6],
    }


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def build_report() -> str:
    now = datetime.now(MSK)
    today = now.date()
    yesterday = today - timedelta(days=1)

    # данные листов менеджеров
    man_rows = {name: fetch_rows(tid, gid) for name, tid, gid in MANAGERS}
    metrics_rows = [fetch_rows(tid, gid) for tid, gid in DAILY_METRICS]

    per_manager = []
    total_launch = 0.0
    rev_yday = 0.0
    rev_today = 0.0
    for name, _tid, _gid in MANAGERS:
        rows = man_rows[name]
        lt = launch_total(rows)
        per_manager.append((name, lt))
        total_launch += lt
        rev_yday += day_revenue(rows, yesterday.day, yesterday.month)
        rev_today += day_revenue(rows, today.day, today.month)

    m = metrics_for(metrics_rows, yesterday.day, yesterday.month)

    greeting = random.choice(GREETINGS)
    yday = yesterday.strftime("%d.%m")
    tday = today.strftime("%d.%m")
    lines = [
        greeting,
        "",
        "Делюсь отчётом по продажам к текущему моменту 📊",
        "",
        f"💰 Получили чистыми вчера ({yday})",
        f"{money(rev_yday)} ₽",
        "",
        f"💰 Получили чистыми сегодня ({tday})",
        f"{money(rev_today)} ₽",
        "",
        "📈 Итоги с 18 мая:",
        f"Чистыми: {money(total_launch)} ₽ 🔥",
        "",
        f"Воронка за вчера ({yday}):",
        f"👥 Лидов всего — {m['leads']:.0f} ·",
        f"🔔 Пуши — {m['push']:.0f} ·",
        f"✅ Продаж — {m['sales']:.0f} ·",
        f"🔕 Игнор — {m['ignore']:.0f} ·",
        f"❌ Отказы — {m['refusal']:.0f} ·",
        f"🌱 Потенциалы — {m['potential']:.0f}",
        "",
        "🏆 Продажи менеджеров с 18 мая:",
    ]
    for name, lt in per_manager:
        lines.append(f"· {name} — {money(lt)} ₽")
    return "\n".join(lines)


async def send_report(bot, chat_id):
    report = await asyncio.to_thread(build_report)
    await bot.send_message(chat_id=chat_id, text=report)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return
    # удобно для настройки REPORT_CHAT_ID — печатаем chat_id в логи
    logger.info("Сообщение из чата chat_id=%s", message.chat_id)

    bot_username = (await context.bot.get_me()).username
    if f"@{bot_username}".lower() not in message.text.lower():
        return

    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    await send_report(context.bot, message.chat_id)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    await send_report(context.bot, REPORT_CHAT_ID)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if REPORT_CHAT_ID:
        app.job_queue.run_daily(daily_job, time=time(hour=12, minute=0, tzinfo=MSK))
        logger.info("Ежедневный отчёт в 12:00 МСК включён для чата %s", REPORT_CHAT_ID)
    else:
        logger.warning("REPORT_CHAT_ID не задан — ежедневная отправка отключена.")

    print("Бот отчёта по продажам запущен.")
    app.run_polling()
