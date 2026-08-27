"""Ежедневный TOP игроков WEKINGS в 23:30 по Молдове.

Берёт данные только из публичного API сайта статистики. Капча/приветствие бота
не затрагиваются.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot

import stats

logger = logging.getLogger("police.today_tops")
TZ = ZoneInfo("Europe/Chisinau")
BASE_URL = os.getenv("WEKINGS_STATS_URL", "https://wekings-statistics.onrender.com").rstrip("/")
STATE_FILE = Path(__file__).resolve().parent / "today_tops_state.json"

# Те же категории, которые пользователь оставил в блоке «Топы вчера».
METRICS = [
    ("glory", "Слава", "🏆", False),
    ("wins", "Победы", "🏅", False),
    ("losses", "Поражения", "💥", True),
    ("dragon_wins", "Победы над Драконом", "🐉", False),
    ("serpent_wins", "Победы над Змеем", "🐍", False),
    ("beasts_killed", "Убито зверей", "🐾", False),
    ("silver_stolen", "Украдено серебра", "💰", False),
    ("silver_lost", "Потеряно серебра", "🪙", True),
    ("crystals_stolen", "Украдено кристаллов", "💎", False),
    ("crystals_lost", "Потеряно кристаллов", "🔹", True),
    ("bandit_wins", "Победы над наёмниками", "⚔️", False),
    ("mine", "Шахта", "⛏️", False),
    ("crusade", "Походы", "🛡️", False),
    ("quests", "Задания", "📜", False),
    ("pet_fights", "Бои питомцев", "🐺", False),
    ("pet_kills", "Победы питомцев", "🐾", False),
    ("garden", "Участок", "🌱", False),
    ("goblins", "Гоблины", "👺", False),
    ("lord_wins", "Победы над Владыкой", "👑", False),
    ("undead_wins", "Победы над нежитью", "☠️", False),
    ("heroes_wins", "Победы над героями", "🗡️", False),
    ("serpent_fights", "Бои со Змеем", "🌊", False),
    ("sent_gifts", "Отправлено подарков", "🎁", False),
    ("fishing", "Рыбалка", "🎣", False),
]


def _load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("Не удалось сохранить today_tops_state.json")


def _local_day(iso_value: str) -> str | None:
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


def _fmt(value: int) -> str:
    return f"{abs(int(value)):,}".replace(",", " ")


async def _json(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    timeout = aiohttp.ClientTimeout(total=35)
    async with session.get(url, params=params, timeout=timeout, headers={"User-Agent": "PoliceBot-WEKINGS/1.0"}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("API статистики вернул неожиданный формат")
        return data


async def build_today_top_text() -> str:
    """Берёт уже рассчитанный блок «Топы сегодня» из API сайта статистики."""
    async with aiohttp.ClientSession() as session:
        data = await _json(session, "/api/today-tops", {"_": int(datetime.now(TZ).timestamp())})

    if not data.get("ready"):
        # Сайт сам знает, готовы ли сегодняшние топы. Не пересчитываем их в боте.
        raise RuntimeError(str(data.get("message") or "Топы сегодня на сайте пока не готовы"))

    tops = data.get("tops") or []
    if not isinstance(tops, list) or not tops:
        raise RuntimeError("API /api/today-tops не вернул список топов")

    rows = []
    negative_metrics = {"losses", "silver_lost", "crystals_lost"}
    for item in tops:
        if not isinstance(item, dict):
            continue
        nick = str(item.get("nickname") or "Игрок")
        label = str(item.get("label") or item.get("metric") or "Показатель")
        icon = str(item.get("icon") or "🏆")
        metric = str(item.get("metric") or "")
        try:
            gain = int(item.get("gain") or 0)
        except (TypeError, ValueError):
            continue
        sign = "−" if metric in negative_metrics else "+"
        rows.append(f"{icon} {label}: <b>{nick}</b>  {sign}{_fmt(gain)}")

    if not rows:
        raise RuntimeError("В Топах сегодня пока нет результатов")

    hero_line = ""
    hero = data.get("hero")
    if isinstance(hero, dict) and hero.get("nickname"):
        first_places = int(hero.get("first_places") or 0)
        hero_line = f"\n👑 Герой дня: <b>{hero['nickname']}</b> — {first_places} первых мест\n"

    date_value = str(data.get("date") or "")
    try:
        shown_date = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    except ValueError:
        shown_date = datetime.now(TZ).strftime("%d.%m.%Y")

    return (
        f"🏆 <b>ТОПЫ СЕГОДНЯ</b>\n"
        f"📅 {shown_date}\n"
        f"{hero_line}\n" + "\n".join(rows)
    )


async def today_tops_worker(bot: Bot) -> None:
    """В 23:30 отправляет TOP. При временной ошибке повторяет до 23:59."""
    while True:
        try:
            now = datetime.now(TZ)
            today = now.date().isoformat()
            state = _load_state()
            due = now.hour == 23 and now.minute >= 30
            if due and state.get("sent_date") != today:
                try:
                    text = await build_today_top_text()
                    delivered = False
                    for chat_id in stats.chat_ids():
                        try:
                            await bot.send_message(chat_id, text, parse_mode="HTML")
                            delivered = True
                        except Exception:
                            logger.exception("TODAY TOP SEND ERROR chat_id=%s", chat_id)
                    if delivered:
                        _save_state({"sent_date": today})
                        logger.info("TODAY TOP SENT date=%s", today)
                except Exception:
                    logger.exception("TODAY TOP BUILD FAILED; повторим через минуту")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("TODAY TOP WORKER ERROR")
        await asyncio.sleep(60)
