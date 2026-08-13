"""Предупреждения группы о приближении Дракона и Морского Змея."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiogram import Bot

import stats


logger = logging.getLogger("police.attack_alerts")

ATTACKS_URL = os.getenv(
    "WEKINGS_ATTACKS_URL",
    "https://wekings-statistics.onrender.com/api/attacks",
).strip()
CHECK_INTERVAL_SECONDS = 30
THRESHOLDS = (60, 30, 4)
STATE_FILE = Path(__file__).resolve().parent / "attack_alerts_state.json"

FUNNY_ALERTS = {
    "Дракона": {
        60: (
            "🐉 Дракон уже прогревает крылья! До его прихода 1 час. Точите мечи, а не языки! ⚔️",
            "🐉 Через час прилетит Дракон. У кого броня в стирке — самое время паниковать! 😄",
            "🐉 До Дракона 1 час! Он уже вылетел, навигатор ведёт прямо к нам. ⚔️",
            "🐉 Дракон будет через час. Собираемся, пока он не решил, что мы доставка еды! 🔥",
        ),
        30: (
            "🐉 Полчаса до Дракона! Допивайте чай и изображайте суровых викингов. ⚔️",
            "🐉 Дракон через 30 минут. Он близко, а ваша храбрость ещё грузится! 😄",
            "🐉 Осталось 30 минут! Проверяем мечи, щиты и запасные штаны. 🔥",
            "🐉 Через полчаса Дракон заглянет в гости. Печенье можно не готовить — он любит воинов!",
        ),
        4: (
            "🐉 Четыре минуты до Дракона! Всё, поздно притворяться спящими — в бой! ⚔️",
            "🐉 Дракон будет через 4 минуты. Кто не спрятался — тот герой! 🔥",
            "🐉 До Дракона 4 минуты! Срочно берём меч, щит и самое боевое выражение лица!",
            "🐉 Четыре минуты! Дракон уже паркуется — встречаем без хлеба, но с мечами! 😄",
        ),
    },
    "Морского Змея": {
        60: (
            "🐍 Морской Змей выплыл по наши души! До встречи 1 час. Готовьте гарпуны! ⚔️",
            "🐍 Через час приплывёт Морской Змей. Просьба не кормить — он и так наглый! 😄",
            "🐍 До Змея 1 час! Он уже в пути, просто плывёт без навигатора. 🌊",
            "🐍 Морской Змей будет через час. Проверяем оружие и учимся не кричать «мамочки»!",
        ),
        30: (
            "🐍 Полчаса до Морского Змея! Сушим носки, заряжаем мечи. ⚔️",
            "🐍 Змей через 30 минут. Вода уже подозрительно шевелится! 🌊",
            "🐍 Осталось полчаса! Кто хотел искупаться — планы лучше перенести. 😄",
            "🐍 Через 30 минут приплывёт Змей. Встречаем тепло — огнём и сталью!",
        ),
        4: (
            "🐍 Четыре минуты до Морского Змея! Купальный сезон официально закрыт — в бой! ⚔️",
            "🐍 Змей будет через 4 минуты. Он уже у берега и явно не загорать! 🌊",
            "🐍 До Змея 4 минуты! Хватайте оружие, удочки сегодня не помогут! 😄",
            "🐍 Четыре минуты! Морской Змей подплывает — встречаем всей суровой компанией!",
        ),
    },
}

EXTRA_FUNNY_ALERTS = {
    "Дракона": {
        60: (
            "До Дракона час! Объявляется перекличка: смелые — в строй, остальные тоже в строй!",
            "Дракон прибудет через час. Видимо, опять летит эконом-классом.",
            "Через час Дракон проверит, кто тут настоящий викинг, а кто просто аватарку поставил!",
            "До Дракона 60 минут. Есть время наточить меч и придумать героическую отговорку!",
        ),
        30: (
            "До Дракона полчаса! Кто обещал прийти «через пять минут» — пора выходить.",
            "Дракон будет через 30 минут. Группа поддержки уже может начинать нервничать!",
            "Полчаса до прилёта! Дракон просил передать: слабых не тронет — он их не заметит.",
            "Через 30 минут Дракон. Время надеть броню правильной стороной!",
        ),
        4: (
            "До Дракона 4 минуты! Если меч потерялся — берите хотя бы суровый взгляд.",
            "Дракон через четыре минуты. Последний шанс закончить фразу: «А вот раньше я бы…»",
            "Четыре минуты до Дракона! Он уже видит чат и смеётся — исправляем ситуацию!",
            "До прилёта 4 минуты. Всё, совещание окончено, начинается жаркое!",
        ),
    },
    "Морского Змея": {
        60: (
            "До Морского Змея час! Надувайте круги… хотя нет, лучше доставайте мечи.",
            "Змей приплывёт через час. Говорит, соскучился. Мы — не очень.",
            "Через час Морской Змей устроит заплыв с препятствиями. Препятствия — это мы!",
            "До Змея 60 минут. Есть время выучить главное морское заклинание: «Бей его!»",
        ),
        30: (
            "До Морского Змея полчаса! Кто боится воды — стойте ближе к мечам.",
            "Змей будет через 30 минут. Плывёт быстро: видимо, штраф за опоздание.",
            "Полчаса до Змея! Он заказал столик на берегу, но получит по шлему.",
            "Через 30 минут Морской Змей. Самое время перестать изображать мирную рыбалку!",
        ),
        4: (
            "До Змея 4 минуты! Он уже машет хвостом — не машите в ответ, берите оружие.",
            "Морской Змей через четыре минуты. Рыбаки, сегодня улов будет кусаться!",
            "Четыре минуты до Змея! Берег занят, броня надета, паника по расписанию.",
            "До приплытия 4 минуты. Змей близко — пора показать, кто здесь главный гад!",
        ),
    },
}

for _event_name, _thresholds in EXTRA_FUNNY_ALERTS.items():
    for _threshold, _variants in _thresholds.items():
        FUNNY_ALERTS[_event_name][_threshold] += _variants

_last_message: dict[tuple[str, int], str] = {}


def _load_sent() -> set[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(value) for value in data.get("sent", [])}
    except (OSError, ValueError, TypeError):
        return set()


def _save_sent(sent: set[str]) -> None:
    # Храним только последние события, чтобы файл не рос бесконечно.
    values = sorted(sent)[-100:]
    try:
        STATE_FILE.write_text(
            json.dumps({"sent": values}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("Не удалось сохранить историю предупреждений")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _message(event_name: str, threshold: int) -> str:
    variants = FUNNY_ALERTS[event_name][threshold]
    previous = _last_message.get((event_name, threshold))
    available = [text for text in variants if text != previous] or list(variants)
    selected = random.choice(available)
    _last_message[(event_name, threshold)] = selected
    selected = selected.lstrip("🐉🐍 ")
    selected = ("🔴🐉 " if event_name == "Дракона" else "🟢🐍 ") + selected
    return selected


async def _fetch_schedule(session: aiohttp.ClientSession) -> dict:
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.get(ATTACKS_URL, timeout=timeout) as response:
        response.raise_for_status()
        return await response.json()


async def attack_alert_worker(bot: Bot) -> None:
    """Проверяет таймеры и отправляет каждую отметку только один раз."""
    sent = _load_sent()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                data = await _fetch_schedule(session)
                if not data.get("error"):
                    now = datetime.now(timezone.utc)
                    events = (
                        ("Дракона", _parse_time(data.get("dragon_at"))),
                        ("Морского Змея", _parse_time(data.get("serpent_at"))),
                    )
                    changed = False
                    for event_name, event_at in events:
                        if event_at is None:
                            continue
                        seconds_left = (event_at - now).total_seconds()
                        if seconds_left <= 0:
                            continue
                        minutes_left = seconds_left / 60
                        event_key = event_at.isoformat()
                        for threshold in THRESHOLDS:
                            # Окно в 3 минуты позволяет пережить сон Render,
                            # но не отправляет давно просроченные предупреждения.
                            if threshold - 3 < minutes_left <= threshold:
                                key = f"{event_name}|{event_key}|{threshold}"
                                if key in sent:
                                    continue
                                delivered = False
                                for chat_id in stats.chat_ids():
                                    try:
                                        await bot.send_message(
                                            chat_id, _message(event_name, threshold)
                                        )
                                        delivered = True
                                    except Exception:
                                        logger.exception(
                                            "ATTACK ALERT SEND ERROR chat_id=%s", chat_id
                                        )
                                if delivered:
                                    sent.add(key)
                                    changed = True
                    if changed:
                        _save_sent(sent)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Не удалось проверить время Дракона и Змея")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
