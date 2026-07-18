"""Автоматические новости для Police Bot Ultimate.

Команды доступны создателю группы, Michel и Юре. Новости сохраняются в JSON,
поэтому расписание продолжается после перезапуска Render.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


BASE_DIR = Path(__file__).resolve().parent
NEWS_PATH = BASE_DIR / "auto_news.json"
OWNER_USERNAMES = {"michel54543", "darkboogimen"}
ACCESS_DENIED = "⛔ Эта команда доступна только создателю группы, Юре и Мишелю."

INTERVALS: dict[str, tuple[str, int]] = {
    "once": ("Один раз", 0),
    "30m": ("Каждые 30 минут", 30 * 60),
    "1h": ("Каждый час", 60 * 60),
    "3h": ("Каждые 3 часа", 3 * 60 * 60),
    "6h": ("Каждые 6 часов", 6 * 60 * 60),
    "12h": ("Каждые 12 часов", 12 * 60 * 60),
    "1d": ("Каждый день", 24 * 60 * 60),
    "1w": ("Каждую неделю", 7 * 24 * 60 * 60),
}

_lock = asyncio.Lock()
_worker_task: asyncio.Task | None = None
_bot: Bot | None = None


class NewsWizard(StatesGroup):
    waiting_content = State()
    waiting_interval = State()
    waiting_confirm = State()


@dataclass
class NewsItem:
    id: int
    chat_id: int
    creator_id: int
    creator_name: str
    kind: str
    text: str
    photo_file_id: str | None
    interval_key: str
    interval_seconds: int
    active: bool
    next_run: float
    publish_count: int
    created_at: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        return cls(
            id=int(data["id"]),
            chat_id=int(data["chat_id"]),
            creator_id=int(data.get("creator_id", 0)),
            creator_name=str(data.get("creator_name", "")),
            kind=str(data.get("kind", "text")),
            text=str(data.get("text", "")),
            photo_file_id=data.get("photo_file_id"),
            interval_key=str(data.get("interval_key", "once")),
            interval_seconds=int(data.get("interval_seconds", 0)),
            active=bool(data.get("active", True)),
            next_run=float(data.get("next_run", time.time())),
            publish_count=int(data.get("publish_count", 0)),
            created_at=float(data.get("created_at", time.time())),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _load_items() -> list[NewsItem]:
    if not NEWS_PATH.exists():
        return []
    try:
        raw = json.loads(NEWS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        return [NewsItem.from_dict(item) for item in raw if isinstance(item, dict)]
    except Exception as error:
        logging.exception("Не удалось загрузить авто-новости: %r", error)
        return []


def _save_items(items: list[NewsItem]) -> None:
    temp = NEWS_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(NEWS_PATH)


def _next_id(items: list[NewsItem]) -> int:
    return max((item.id for item in items), default=0) + 1


async def _is_allowed(bot: Bot, message: Message) -> bool:
    if not message.from_user:
        return False
    username = (message.from_user.username or "").lower()
    if username in OWNER_USERNAMES:
        return True
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type not in {"group", "supergroup"}:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        status = getattr(member.status, "value", member.status)
        return status == "creator"
    except Exception as error:
        logging.exception("Не удалось проверить создателя группы: %r", error)
        return False


async def _require_allowed(bot: Bot, message: Message) -> bool:
    if await _is_allowed(bot, message):
        return True
    await message.answer(ACCESS_DENIED)
    return False


def _interval_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Один раз", callback_data="news_interval:once")],
        [
            InlineKeyboardButton(text="30 минут", callback_data="news_interval:30m"),
            InlineKeyboardButton(text="1 час", callback_data="news_interval:1h"),
        ],
        [
            InlineKeyboardButton(text="3 часа", callback_data="news_interval:3h"),
            InlineKeyboardButton(text="6 часов", callback_data="news_interval:6h"),
        ],
        [
            InlineKeyboardButton(text="12 часов", callback_data="news_interval:12h"),
            InlineKeyboardButton(text="Каждый день", callback_data="news_interval:1d"),
        ],
        [InlineKeyboardButton(text="Каждую неделю", callback_data="news_interval:1w")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="news_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Запустить", callback_data="news_confirm")],
            [InlineKeyboardButton(text="✏️ Изменить интервал", callback_data="news_change_interval")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="news_cancel")],
        ]
    )


async def _send_item(bot: Bot, item: NewsItem) -> None:
    if item.kind == "photo" and item.photo_file_id:
        await bot.send_photo(item.chat_id, item.photo_file_id, caption=item.text or None)
    else:
        await bot.send_message(item.chat_id, item.text)


async def _publish_by_id(bot: Bot, news_id: int, chat_id: int | None = None) -> bool:
    async with _lock:
        items = _load_items()
        item = next(
            (x for x in items if x.id == news_id and (chat_id is None or x.chat_id == chat_id)),
            None,
        )
    if not item:
        return False
    await _send_item(bot, item)
    async with _lock:
        items = _load_items()
        current = next((x for x in items if x.id == news_id), None)
        if current:
            current.publish_count += 1
            if current.interval_seconds > 0:
                current.next_run = time.time() + current.interval_seconds
            _save_items(items)
    return True


async def scheduler_worker() -> None:
    assert _bot is not None
    while True:
        try:
            now = time.time()
            async with _lock:
                items = _load_items()
                due_ids = [item.id for item in items if item.active and item.next_run <= now]
            for news_id in due_ids:
                async with _lock:
                    items = _load_items()
                    item = next((x for x in items if x.id == news_id and x.active), None)
                if not item:
                    continue
                try:
                    await _send_item(_bot, item)
                except Exception as error:
                    logging.exception("Ошибка публикации новости #%s: %r", news_id, error)
                    # Повтор через минуту, чтобы временная ошибка Telegram не потеряла публикацию.
                    async with _lock:
                        items = _load_items()
                        current = next((x for x in items if x.id == news_id), None)
                        if current:
                            current.next_run = time.time() + 60
                            _save_items(items)
                    continue

                async with _lock:
                    items = _load_items()
                    current = next((x for x in items if x.id == news_id), None)
                    if not current:
                        continue
                    current.publish_count += 1
                    if current.interval_seconds <= 0:
                        current.active = False
                    else:
                        current.next_run = time.time() + current.interval_seconds
                    _save_items(items)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.exception("Ошибка планировщика авто-новостей: %r", error)
        await asyncio.sleep(10)


async def start_worker(bot: Bot) -> None:
    global _worker_task, _bot
    _bot = bot
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(scheduler_worker(), name="auto-news-worker")


async def stop_worker() -> None:
    global _worker_task
    if not _worker_task:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None


def register(dp: Dispatcher, bot: Bot) -> None:
    @dp.message(Command("новость"))
    async def news_command(message: Message, state: FSMContext) -> None:
        if not await _require_allowed(bot, message):
            return
        chat_type = getattr(message.chat.type, "value", message.chat.type)
        if chat_type not in {"group", "supergroup"}:
            await message.answer("ℹ️ Создавать новости нужно командой /новость прямо в группе.")
            return
        await state.clear()
        await state.set_state(NewsWizard.waiting_content)
        await state.update_data(target_chat_id=message.chat.id)
        await message.answer(
            "📝 Отправьте текст новости или фотографию с подписью.\n\n"
            "Для отмены используйте /отмена."
        )

    @dp.message(Command("отмена"))
    async def cancel_command(message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        if current and current.startswith(NewsWizard.__name__):
            await state.clear()
            await message.answer("❌ Создание новости отменено.")

    @dp.message(NewsWizard.waiting_content)
    async def news_content(message: Message, state: FSMContext) -> None:
        if not await _require_allowed(bot, message):
            await state.clear()
            return
        if message.photo:
            await state.update_data(
                kind="photo",
                photo_file_id=message.photo[-1].file_id,
                text=message.caption or "",
            )
        elif message.text and not message.text.startswith("/"):
            await state.update_data(kind="text", photo_file_id=None, text=message.text)
        else:
            await message.answer("⚠️ Отправьте обычный текст или фотографию с подписью.")
            return
        await state.set_state(NewsWizard.waiting_interval)
        await message.answer("⏰ Выберите интервал публикации:", reply_markup=_interval_keyboard())

    @dp.callback_query(NewsWizard.waiting_interval, F.data.startswith("news_interval:"))
    async def interval_callback(callback: CallbackQuery, state: FSMContext) -> None:
        key = callback.data.split(":", 1)[1]
        if key not in INTERVALS:
            await callback.answer("Неизвестный интервал", show_alert=True)
            return
        label, seconds = INTERVALS[key]
        await state.update_data(interval_key=key, interval_seconds=seconds)
        data = await state.get_data()
        preview = data.get("text") or "(фотография без подписи)"
        if len(preview) > 700:
            preview = preview[:697] + "..."
        await state.set_state(NewsWizard.waiting_confirm)
        await callback.message.edit_text(
            "📋 Предпросмотр новости\n\n"
            f"{preview}\n\n"
            f"⏰ Интервал: {label}\n"
            "🚀 После запуска первая публикация будет выполнена сразу.",
            reply_markup=_confirm_keyboard(),
        )
        await callback.answer()

    @dp.callback_query(NewsWizard.waiting_confirm, F.data == "news_change_interval")
    async def change_interval(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(NewsWizard.waiting_interval)
        await callback.message.edit_text("⏰ Выберите новый интервал:", reply_markup=_interval_keyboard())
        await callback.answer()

    @dp.callback_query(F.data == "news_cancel")
    async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text("❌ Создание новости отменено.")
        await callback.answer()

    @dp.callback_query(NewsWizard.waiting_confirm, F.data == "news_confirm")
    async def confirm_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user:
            return
        username = (callback.from_user.username or "").lower()
        allowed = username in OWNER_USERNAMES
        if not allowed:
            try:
                member = await bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
                allowed = getattr(member.status, "value", member.status) == "creator"
            except Exception:
                allowed = False
        if not allowed:
            await callback.answer(ACCESS_DENIED, show_alert=True)
            return

        data = await state.get_data()
        now = time.time()
        async with _lock:
            items = _load_items()
            item = NewsItem(
                id=_next_id(items),
                chat_id=int(data["target_chat_id"]),
                creator_id=callback.from_user.id,
                creator_name=callback.from_user.full_name,
                kind=str(data.get("kind", "text")),
                text=str(data.get("text", "")),
                photo_file_id=data.get("photo_file_id"),
                interval_key=str(data["interval_key"]),
                interval_seconds=int(data["interval_seconds"]),
                active=True,
                next_run=now,
                publish_count=0,
                created_at=now,
            )
            items.append(item)
            _save_items(items)
        await state.clear()
        label = INTERVALS[item.interval_key][0]
        await callback.message.edit_text(
            f"✅ Новость #{item.id} запущена.\n"
            f"⏰ Интервал: {label}\n"
            "🚀 Первая публикация появится сейчас."
        )
        await callback.answer("Новость запущена")

    @dp.message(Command("новости"))
    async def list_news(message: Message) -> None:
        if not await _require_allowed(bot, message):
            return
        async with _lock:
            items = [x for x in _load_items() if x.chat_id == message.chat.id]
        if not items:
            await message.answer("📭 В этой группе ещё нет сохранённых новостей.")
            return
        lines = ["📰 Новости этой группы:"]
        for item in items[-20:]:
            status = "🟢 активна" if item.active else "⏸ остановлена"
            label = INTERVALS.get(item.interval_key, (item.interval_key, 0))[0]
            preview = (item.text or "Фото без подписи").replace("\n", " ")
            if len(preview) > 55:
                preview = preview[:52] + "..."
            lines.append(
                f"\n#{item.id} — {status}\n"
                f"⏰ {label} · публикаций: {item.publish_count}\n"
                f"{preview}"
            )
        lines.append(
            "\nУправление: /новостьсейчас ID, /стопновость ID, "
            "/возобновитьновость ID, /удалитьновость ID"
        )
        await message.answer("\n".join(lines))

    async def get_command_id(message: Message) -> int | None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            await message.answer("⚠️ Укажите номер новости. Например: /стопновость 2")
            return None
        return int(parts[1].strip())

    @dp.message(Command("новостьсейчас"))
    async def publish_now(message: Message) -> None:
        if not await _require_allowed(bot, message):
            return
        news_id = await get_command_id(message)
        if news_id is None:
            return
        try:
            found = await _publish_by_id(bot, news_id, message.chat.id)
        except Exception as error:
            logging.exception("Не удалось опубликовать новость сейчас: %r", error)
            await message.answer("⚠️ Telegram временно не позволил опубликовать новость.")
            return
        await message.answer("✅ Новость опубликована.") if found else await message.answer("❌ Новость с таким номером не найдена.")

    async def change_active(message: Message, active: bool) -> None:
        if not await _require_allowed(bot, message):
            return
        news_id = await get_command_id(message)
        if news_id is None:
            return
        async with _lock:
            items = _load_items()
            item = next((x for x in items if x.id == news_id and x.chat_id == message.chat.id), None)
            if not item:
                await message.answer("❌ Новость с таким номером не найдена.")
                return
            item.active = active
            if active:
                item.next_run = time.time() + (item.interval_seconds or 1)
            _save_items(items)
        await message.answer("▶️ Публикация возобновлена." if active else "⏸ Публикация остановлена.")

    @dp.message(Command("стопновость"))
    async def stop_news(message: Message) -> None:
        await change_active(message, False)

    @dp.message(Command("возобновитьновость"))
    async def resume_news(message: Message) -> None:
        await change_active(message, True)

    @dp.message(Command("удалитьновость"))
    async def delete_news(message: Message) -> None:
        if not await _require_allowed(bot, message):
            return
        news_id = await get_command_id(message)
        if news_id is None:
            return
        async with _lock:
            items = _load_items()
            remaining = [x for x in items if not (x.id == news_id and x.chat_id == message.chat.id)]
            if len(remaining) == len(items):
                await message.answer("❌ Новость с таким номером не найдена.")
                return
            _save_items(remaining)
        await message.answer(f"🗑 Новость #{news_id} удалена.")
