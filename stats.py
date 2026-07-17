"""Суточная статистика Police Bot.

Счётчики хранятся рядом с проектом в stats.json. Путь можно изменить
переменной окружения STATS_PATH. В 23:59 по времени Молдовы бот публикует
суточную сводку в известных группах, а после наступления нового дня
сбрасывает суточные счётчики.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
STATS_PATH = Path(os.getenv("STATS_PATH", str(BASE_DIR / "stats.json")))
MOLDOVA_TZ = ZoneInfo("Europe/Chisinau")
_LOCK = threading.RLock()


def _today() -> str:
    return datetime.now(MOLDOVA_TZ).date().isoformat()


_DEFAULTS: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "date": _today(),
    "captcha_passed": 0,
    "captcha_failed": 0,
    "ads_removed": 0,
    "report_sent_date": "",
    "chat_ids": [],
}


def _normalize(data: Any) -> dict[str, Any]:
    result = dict(_DEFAULTS)
    if isinstance(data, dict):
        started_at = data.get("started_at")
        if isinstance(started_at, str) and started_at.strip():
            result["started_at"] = started_at.strip()
        date_value = data.get("date")
        if isinstance(date_value, str) and date_value.strip():
            result["date"] = date_value.strip()
        report_date = data.get("report_sent_date")
        if isinstance(report_date, str):
            result["report_sent_date"] = report_date.strip()
        for key in ("captcha_passed", "captcha_failed", "ads_removed"):
            try:
                result[key] = max(0, int(data.get(key, 0)))
            except (TypeError, ValueError):
                result[key] = 0
        chat_ids: list[int] = []
        raw_ids = data.get("chat_ids", [])
        if isinstance(raw_ids, list):
            for item in raw_ids:
                try:
                    chat_id = int(item)
                except (TypeError, ValueError):
                    continue
                if chat_id not in chat_ids:
                    chat_ids.append(chat_id)
        result["chat_ids"] = chat_ids
    return result


def _read_raw() -> dict[str, Any]:
    if not STATS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        return _normalize(json.loads(STATS_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def _write(data: dict[str, Any]) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATS_PATH.with_suffix(STATS_PATH.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(_normalize(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(STATS_PATH)


def _rollover_if_needed(data: dict[str, Any]) -> dict[str, Any]:
    today = _today()
    if data.get("date") != today:
        data["date"] = today
        data["captcha_passed"] = 0
        data["captcha_failed"] = 0
        data["ads_removed"] = 0
        data["report_sent_date"] = ""
    return data


def ensure_file() -> None:
    with _LOCK:
        data = _rollover_if_needed(_read_raw())
        try:
            _write(data)
        except OSError:
            pass


def register_chat(chat_id: int) -> None:
    with _LOCK:
        data = _rollover_if_needed(_read_raw())
        chats = list(data.get("chat_ids", []))
        chat_id = int(chat_id)
        if chat_id not in chats:
            chats.append(chat_id)
            data["chat_ids"] = chats
        try:
            _write(data)
        except OSError:
            pass


def increment(field: str, amount: int = 1, *, chat_id: int | None = None) -> None:
    if field not in {"captcha_passed", "captcha_failed", "ads_removed"}:
        raise ValueError(f"Неизвестный счётчик: {field}")
    with _LOCK:
        data = _rollover_if_needed(_read_raw())
        data[field] = max(0, int(data.get(field, 0)) + int(amount))
        if chat_id is not None:
            chats = list(data.get("chat_ids", []))
            value = int(chat_id)
            if value not in chats:
                chats.append(value)
                data["chat_ids"] = chats
        try:
            _write(data)
        except OSError:
            pass


def snapshot() -> dict[str, Any]:
    with _LOCK:
        data = _rollover_if_needed(_read_raw())
        try:
            _write(data)
        except OSError:
            pass
        return data


def chat_ids() -> list[int]:
    return list(snapshot().get("chat_ids", []))


def report_already_sent() -> bool:
    data = snapshot()
    return data.get("report_sent_date") == data.get("date")


def mark_report_sent() -> None:
    with _LOCK:
        data = _rollover_if_needed(_read_raw())
        data["report_sent_date"] = str(data.get("date", _today()))
        try:
            _write(data)
        except OSError:
            pass


def service_duration() -> tuple[int, int]:
    data = snapshot()
    try:
        start = datetime.fromisoformat(str(data["started_at"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    except (TypeError, ValueError):
        seconds = 0
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    return days, hours


def police_text() -> str:
    data = snapshot()
    days, hours = service_duration()
    duration = f"{days} сут. {hours} ч."
    return (
        "👮 Police Bot Ultimate\n\n"
        "🟢 Статус: На посту\n\n"
        f"📅 Статистика за сегодня ({data['date']})\n\n"
        f"⏳ На службе: {duration}\n\n"
        f"🛡 Прошли капчу: {data['captcha_passed']}\n\n"
        f"🚫 Не прошли капчу: {data['captcha_failed']}\n\n"
        f"🚷 Удалено рекламщиков: {data['ads_removed']}"
    )


def daily_report_text() -> str:
    data = snapshot()
    return (
        "👮 Полицейская сводка за сегодня\n\n"
        f"🛡 Прошли капчу: {data['captcha_passed']}\n\n"
        f"🚫 Не прошли капчу: {data['captcha_failed']}\n\n"
        f"🚷 Удалено рекламщиков: {data['ads_removed']}\n\n"
        "📅 Через минуту начнётся статистика нового дня."
    )


ensure_file()
