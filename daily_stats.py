import asyncio
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Chisinau")
REPORT_TIME = time(23, 59)
STATS_PATH = Path(__file__).resolve().parent / "daily_stats.json"


def _load() -> dict[str, dict[str, dict[str, int]]]:
    if not STATS_PATH.exists():
        return {}
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


stats = _load()


def _save() -> None:
    temporary = STATS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STATS_PATH)


def record(chat_id: int, field: str) -> None:
    day = datetime.now(TIMEZONE).date().isoformat()
    chat = stats.setdefault(day, {}).setdefault(
        str(chat_id), {"passed": 0, "blocked": 0}
    )
    chat[field] = int(chat.get(field, 0)) + 1
    _save()


def record_passed(chat_id: int) -> None:
    record(chat_id, "passed")


def record_blocked(chat_id: int) -> None:
    record(chat_id, "blocked")


def report_text(day: str, passed: int, blocked: int) -> str:
    return (
        "📊 Статистика за день\n\n"
        f"🟢 Прошли капчу: {passed}\n"
        f"🔴 Удалены за непрохождение: {blocked}\n\n"
        f"Дата: {day}"
    )


async def publish_due(bot: Any) -> None:
    now = datetime.now(TIMEZONE)
    today = now.date().isoformat()
    due_days = [day for day in stats if day < today]
    if now.time() >= REPORT_TIME and today in stats:
        due_days.append(today)

    changed = False
    for day in sorted(set(due_days)):
        chats = stats.get(day, {})
        for chat_id, values in list(chats.items()):
            try:
                await bot.send_message(
                    int(chat_id),
                    report_text(
                        day,
                        int(values.get("passed", 0)),
                        int(values.get("blocked", 0)),
                    ),
                )
            except Exception as error:
                print("Ошибка отправки дневной статистики:", repr(error))
                continue
            chats.pop(chat_id, None)
            changed = True
        if not chats:
            stats.pop(day, None)
    if changed:
        _save()


async def reporter(bot: Any) -> None:
    """Проверяет отчёт каждые 20 секунд. Если бот был перезапущен,
    неотправленный отчёт за прошедший день будет доставлен после запуска.
    """
    while True:
        await publish_due(bot)
        await asyncio.sleep(20)
