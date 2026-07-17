import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any
from collections import deque

from aiogram import Bot
try:
    from aiogram.exceptions import TelegramRetryAfter
except (ImportError, ModuleNotFoundError):  # test stubs / старые окружения
    class TelegramRetryAfter(Exception):
        retry_after = 1
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from utils import chunk_buttons, safe_delete_message
import stats


BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = BASE_DIR / "captcha_questions.json"
STATE_PATH = BASE_DIR / "join_state.json"
RAID_STATE_PATH = BASE_DIR / "raid_state.json"
CAPTCHA_TIMEOUT_SECONDS = 120
HIDDEN_CAPTCHA_TIMEOUT_SECONDS = 5 * 60
GROUP_NOTICE_SECONDS = 15
RAID_WINDOW_SECONDS = 10
RAID_THRESHOLD = 16
RAID_COOLDOWN_SECONDS = 30
JOIN_WORKERS = 3
QUEUE_MAXSIZE = 10000
GROUP_NOTICE_TEXT = (
    "🔒 Чтобы получить доступ к чату, откройте личный чат с ботом, "
    "нажмите Start и пройдите проверку."
)
WELCOME_TEXT = (
    "🛡 Добро пожаловать в Группу!\n\n"
    "Пусть Евгений будет на вашей стороне! ⚔️\n\n"
    "Не флудите, не спамьте и приятного общения! 🍻"
)

pending: dict[tuple[int, int], dict[str, Any]] = {}
processing_users: set[tuple[int, int]] = set()
timeout_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
shared_notice_messages: dict[int, int] = {}
shared_notice_locks: dict[int, asyncio.Lock] = {}
# Совместимость со старой очисткой состояния в тестах и при обновлении.
group_notice_messages = shared_notice_messages
group_notice_tasks: dict[int, asyncio.Task[None]] = {}
BOT_ID: int | None = None
BOT_USERNAME: str | None = None

join_queue: asyncio.Queue[tuple[Bot, int, User]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
queued_users: set[tuple[int, int]] = set()
worker_tasks: list[asyncio.Task[None]] = []
raid_monitor_task: asyncio.Task[None] | None = None
join_timestamps: deque[float] = deque()
raid_mode = False
raid_forced = False
raid_started_at: float | None = None
last_join_at: float | None = None
raid_total = 0
raid_passed = 0
raid_removed = 0
raid_bot: Bot | None = None
raid_chat_id: int | None = None
raid_alert_task: asyncio.Task[None] | None = None
RAID_ALERT_DELETE_SECONDS = 120


def save_raid_state() -> None:
    data = {
        "raid_mode": raid_mode,
        "raid_forced": raid_forced,
        "raid_started_at": raid_started_at,
        "last_join_at": last_join_at,
        "raid_total": raid_total,
        "raid_passed": raid_passed,
        "raid_removed": raid_removed,
    }
    try:
        RAID_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        logging.exception("RAID STATE SAVE ERROR: %r", error)


def load_raid_state() -> None:
    global raid_mode, raid_forced, raid_started_at, last_join_at
    global raid_total, raid_passed, raid_removed
    if not RAID_STATE_PATH.exists():
        return
    try:
        data = json.loads(RAID_STATE_PATH.read_text(encoding="utf-8"))
        raid_forced = bool(data.get("raid_forced", False))
        raid_mode = raid_forced
        raid_started_at = data.get("raid_started_at") if raid_forced else None
        last_join_at = data.get("last_join_at")
        raid_total = int(data.get("raid_total", 0))
        raid_passed = int(data.get("raid_passed", 0))
        raid_removed = int(data.get("raid_removed", 0))
    except Exception as error:
        logging.exception("RAID STATE LOAD ERROR: %r", error)


def set_raid_mode(enabled: bool, forced: bool = False) -> None:
    global raid_mode, raid_forced, raid_started_at, raid_total, raid_passed, raid_removed
    if enabled:
        if not raid_mode:
            raid_started_at = time.time()
            raid_total = raid_passed = raid_removed = 0
            logging.warning("ANTI RAID MODE ENABLED forced=%s", forced)
        raid_mode = True
        raid_forced = forced
    else:
        if raid_mode:
            logging.warning("ANTI RAID MODE DISABLED")
        raid_mode = False
        raid_forced = False
        raid_started_at = None
    save_raid_state()


def register_join_spike() -> None:
    global last_join_at, raid_total
    now = time.time()
    last_join_at = now
    join_timestamps.append(now)
    while join_timestamps and join_timestamps[0] < now - RAID_WINDOW_SECONDS:
        join_timestamps.popleft()
    if raid_mode:
        raid_total += 1
    elif len(join_timestamps) >= RAID_THRESHOLD:
        set_raid_mode(True, forced=False)
        raid_total = len(join_timestamps)
        save_raid_state()


def raid_status_text() -> str:
    mode = "ОСАДА" if raid_mode else "Обычный"
    forced = " (вручную)" if raid_forced and raid_mode else ""
    return (
        f"🛡 Режим: {mode}{forced}\n"
        f"📥 Очередь: {join_queue.qsize()}\n"
        f"⚙️ Обработчиков: {JOIN_WORKERS}\n"
        f"👥 Получено во время осады: {raid_total}\n"
        f"✅ Прошли: {raid_passed}\n"
        f"🚫 Удалено: {raid_removed}"
    )


async def raid_monitor() -> None:
    while True:
        try:
            await asyncio.sleep(5)
            if raid_mode and not raid_forced and last_join_at is not None:
                if time.time() - last_join_at >= RAID_COOLDOWN_SECONDS and join_queue.empty():
                    if raid_bot is not None and raid_chat_id is not None:
                        await send_raid_finished_alert(raid_bot, raid_chat_id)
                    set_raid_mode(False)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.exception("RAID MONITOR ERROR: %r", error)



async def _delete_raid_alert_later(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await asyncio.sleep(RAID_ALERT_DELETE_SECONDS)
        await safe_delete_message(bot, chat_id, message_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logging.exception("RAID ALERT DELETE ERROR chat_id=%s: %r", chat_id, error)


async def send_raid_started_alert(bot: Bot, chat_id: int) -> None:
    # При осаде не создаём отдельное сообщение: обновляем общую кнопку проверки.
    try:
        await refresh_shared_notice(bot, chat_id, force_show=True)
    except Exception as error:
        logging.exception("RAID START ALERT ERROR chat_id=%s: %r", chat_id, error)


async def send_raid_finished_alert(bot: Bot, chat_id: int) -> None:
    global raid_alert_task
    await _delete_shared_notice(bot, chat_id)
    text = (
        "🏆 АТАКА ОТРАЖЕНА!\n\n"
        "✅ Режим «ОСАДА» завершён.\n"
        f"👥 Получено вступлений: {raid_total}\n"
        f"✅ Прошли проверку: {raid_passed}\n"
        f"🚫 Удалено: {raid_removed}\n\n"
        "🛡 Группа снова работает в обычном режиме."
    )
    try:
        message = await bot.send_message(chat_id, text)
        if raid_alert_task is not None and not raid_alert_task.done():
            raid_alert_task.cancel()
        raid_alert_task = asyncio.create_task(_delete_raid_alert_later(bot, chat_id, message.message_id))
    except Exception as error:
        logging.exception("RAID END ALERT ERROR chat_id=%s: %r", chat_id, error)

def load_questions() -> list[dict[str, Any]]:
    with QUESTIONS_PATH.open("r", encoding="utf-8") as file:
        questions = json.load(file)
    if not isinstance(questions, list) or not questions:
        raise ValueError("captcha_questions.json must contain a non-empty list")
    return questions


QUESTIONS = load_questions()


def set_bot_identity(bot_id: int | None, bot_username: str | None = None) -> None:
    global BOT_ID, BOT_USERNAME
    BOT_ID = bot_id
    BOT_USERNAME = (bot_username or "").lstrip("@") or None


def set_bot_id(bot_id: int | None) -> None:
    """Обратная совместимость со старыми вызовами и тестами."""
    set_bot_identity(bot_id, BOT_USERNAME)


def member_status(value: object) -> str:
    return str(getattr(value, "value", value))


def member_is_present(chat_member: object) -> bool:
    """Return whether the user is actually present in the chat.

    Telegram can report both the old and new status as ``restricted`` while
    only ``is_member`` changes from False to True. Looking only at the status
    therefore misses some joins made through an invite link.
    """
    status = member_status(getattr(chat_member, "status", ""))
    if status in {"member", "administrator", "creator"}:
        return True
    if status == "restricted":
        return bool(getattr(chat_member, "is_member", False))
    return False


def user_just_joined(event: ChatMemberUpdated) -> bool:
    return (
        not member_is_present(event.old_chat_member)
        and member_is_present(event.new_chat_member)
    )


def user_just_left(event: ChatMemberUpdated) -> bool:
    return (
        member_is_present(event.old_chat_member)
        and not member_is_present(event.new_chat_member)
    )


def is_pending(chat_id: int, user_id: int) -> bool:
    return (chat_id, user_id) in pending


def full_mute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )


def full_unmute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )


def make_question() -> tuple[dict[str, Any], InlineKeyboardMarkup, int]:
    question = random.choice(QUESTIONS)
    answers = list(enumerate(question["answers"]))
    random.shuffle(answers)
    correct = next(index for index, (old_index, _) in enumerate(answers) if old_index == question["correct"])
    buttons = [
        InlineKeyboardButton(text=str(answer), callback_data=f"captcha:{index}")
        for index, (_, answer) in enumerate(answers)
    ]
    return question, InlineKeyboardMarkup(inline_keyboard=chunk_buttons(buttons, 1)), correct


def captcha_text(question: dict[str, Any]) -> str:
    return (
        "🛡 Police | Проверка нового участника\n\n"
        "Для защиты группы ответьте на вопрос.\n\n"
        "🟨 ВОПРОС\n\n"
        f"{question['question']}\n\n"
        "Выберите правильный ответ."
    )


def state_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (chat_id, user_id), data in pending.items():
        rows.append(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "message_id": int(data.get("message_id", 0)),
                "message_chat_id": int(data.get("message_chat_id", 0)),
                "correct": int(data["correct"]),
                "deadline": float(data["deadline"]),
                "delivery": str(data.get("delivery", "private")),
            }
        )
    return rows


def save_state() -> None:
    try:
        STATE_PATH.write_text(
            json.dumps(state_rows(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:
        logging.exception("JOIN STATE SAVE ERROR: %r", error)


def load_state() -> list[dict[str, Any]]:
    if not STATE_PATH.exists():
        return []
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logging.exception("JOIN STATE LOAD ERROR: %r", error)
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def cancel_timer(chat_id: int, user_id: int) -> None:
    task = timeout_tasks.pop((chat_id, user_id), None)
    if task is not None:
        task.cancel()


def clear_user(chat_id: int, user_id: int) -> None:
    pending.pop((chat_id, user_id), None)
    processing_users.discard((chat_id, user_id))
    cancel_timer(chat_id, user_id)
    save_state()
    schedule_shared_notice_refresh(chat_id)


async def _delete_shared_notice(bot: Bot, chat_id: int) -> None:
    message_id = shared_notice_messages.pop(chat_id, None)
    if message_id is not None:
        await safe_delete_message(bot, chat_id, message_id)


def shared_verification_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Пройти проверку",
                callback_data="verify_shared",
            )
        ]]
    )


def waiting_private_count(chat_id: int) -> int:
    return sum(
        1
        for (pending_chat_id, _), data in pending.items()
        if pending_chat_id == chat_id and str(data.get("delivery")) == "waiting_private"
    )


def shared_notice_text(chat_id: int) -> str:
    count = waiting_private_count(chat_id)
    if raid_mode:
        return (
            "🚨 ВНИМАНИЕ! ОБНАРУЖЕНА МАССОВАЯ АТАКА НА ГРУППУ!\n\n"
            "🛡 Police Bot автоматически включил режим «ОСАДА».\n"
            "🔒 Все новые участники проходят проверку перед доступом к чату.\n\n"
            f"⏳ Ожидают проверку: {count}\n\n"
            "Если вы только что вступили — нажмите кнопку ниже."
        )
    return (
        "🔒 Новые участники ожидают проверки.\n\n"
        f"⏳ В очереди: {count}\n\n"
        "Если вы только что вступили — нажмите кнопку ниже."
    )


async def refresh_shared_notice(bot: Bot, chat_id: int, force_show: bool = False) -> None:
    lock = shared_notice_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        count = waiting_private_count(chat_id)
        message_id = shared_notice_messages.get(chat_id)
        if count <= 0 and not raid_mode and not force_show:
            if message_id is not None:
                await _delete_shared_notice(bot, chat_id)
            return
        text = shared_notice_text(chat_id)
        if message_id is not None:
            try:
                await bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=shared_verification_keyboard(),
                )
                return
            except Exception as error:
                # Не создаём дубликат даже при временной ошибке редактирования.
                logging.info("SHARED NOTICE EDIT FAILED chat_id=%s: %r", chat_id, error)
                return
        try:
            notice = await bot.send_message(
                chat_id,
                text,
                reply_markup=shared_verification_keyboard(),
            )
            shared_notice_messages[chat_id] = notice.message_id
        except Exception as error:
            logging.exception("SHARED NOTICE SEND ERROR chat_id=%s: %r", chat_id, error)


def schedule_shared_notice_refresh(chat_id: int) -> None:
    if raid_bot is None:
        return
    try:
        asyncio.get_running_loop().create_task(refresh_shared_notice(raid_bot, chat_id))
    except RuntimeError:
        pass


async def show_group_notice_once(bot: Bot, chat_id: int, user: User) -> None:
    # Одно общее сообщение на всю очередь, независимо от числа вступивших.
    await refresh_shared_notice(bot, chat_id, force_show=True)


async def handle_verify_button(bot: Bot, callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    matches = [
        chat_id
        for (chat_id, pending_user_id), data in pending.items()
        if pending_user_id == user_id and str(data.get("delivery")) == "waiting_private"
    ]
    if not matches:
        await callback.answer("ℹ️ Проверка вам не требуется.", show_alert=True)
        return
    if not BOT_USERNAME:
        await callback.answer("Откройте личный чат с ботом и нажмите Start.", show_alert=True)
        return
    deep_link = f"https://t.me/{BOT_USERNAME}?start=verify_{matches[0]}_{user_id}"
    await callback.answer(url=deep_link)


async def restrict_user(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=full_mute_permissions(),
    )
    logging.info("RESTRICTED chat_id=%s user_id=%s", chat_id, user_id)


async def unrestrict_user(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=full_unmute_permissions(),
    )


async def kick_user(bot: Bot, chat_id: int, user_id: int, reason: str) -> None:
    global raid_removed
    data = pending.get((chat_id, user_id))
    if data is not None and int(data.get("message_id", 0)):
        message_chat_id = int(data.get("message_chat_id", 0)) or chat_id
        await safe_delete_message(bot, message_chat_id, int(data["message_id"]))
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        logging.info("KICKED chat_id=%s user_id=%s reason=%s", chat_id, user_id, reason)
        if raid_mode:
            raid_removed += 1
            save_raid_state()
        if reason in {"timeout", "wrong_answer"}:
            stats.increment("captcha_failed", chat_id=chat_id)
    except Exception as error:
        logging.exception("KICK ERROR chat_id=%s user_id=%s reason=%s: %r", chat_id, user_id, reason, error)
    clear_user(chat_id, user_id)


async def timeout_worker(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        data = pending.get((chat_id, user_id))
        if not data:
            return
        delay = max(0, float(data["deadline"]) - time.time())
        await asyncio.sleep(delay)
        if (chat_id, user_id) not in pending:
            return
        logging.info("TIMEOUT chat_id=%s user_id=%s", chat_id, user_id)
        await kick_user(bot, chat_id, user_id, "timeout")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logging.exception("TIMEOUT WORKER ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)


def start_timeout(bot: Bot, chat_id: int, user_id: int) -> None:
    cancel_timer(chat_id, user_id)
    timeout_tasks[(chat_id, user_id)] = asyncio.create_task(timeout_worker(bot, chat_id, user_id))


async def send_captcha(bot: Bot, chat_id: int, user: User) -> bool:
    user_id = user.id
    question, keyboard, correct = make_question()
    try:
        captcha_message = await bot.send_message(user_id, captcha_text(question), reply_markup=keyboard)
    except Exception as error:
        logging.info("CAPTCHA PRIVATE SEND FAILED chat_id=%s user_id=%s: %r", chat_id, user_id, error)
        pending[(chat_id, user_id)] = {
            "message_id": 0,
            "message_chat_id": 0,
            "correct": correct,
            "deadline": time.time() + HIDDEN_CAPTCHA_TIMEOUT_SECONDS,
            "delivery": "waiting_private",
        }
        save_state()
        start_timeout(bot, chat_id, user_id)
        await show_group_notice_once(bot, chat_id, user)
        logging.info("CAPTCHA WAITING PRIVATE chat_id=%s user_id=%s", chat_id, user_id)
        return True
    pending[(chat_id, user_id)] = {
        "message_id": captcha_message.message_id,
        "message_chat_id": user_id,
        "correct": correct,
        "deadline": time.time() + HIDDEN_CAPTCHA_TIMEOUT_SECONDS,
        "delivery": "private",
    }
    save_state()
    start_timeout(bot, chat_id, user_id)
    logging.info("CAPTCHA SENT chat_id=%s user_id=%s message_id=%s", chat_id, user_id, captcha_message.message_id)
    return True


async def send_pending_private_captcha(bot: Bot, chat_id: int, user_id: int) -> bool:
    data = pending.get((chat_id, user_id))
    if not data:
        return False
    question, keyboard, correct = make_question()
    try:
        captcha_message = await bot.send_message(user_id, captcha_text(question), reply_markup=keyboard)
    except Exception as error:
        logging.exception("CAPTCHA PRIVATE RETRY ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)
        return False
    data["message_id"] = captcha_message.message_id
    data["message_chat_id"] = user_id
    data["correct"] = correct
    data["delivery"] = "private"
    save_state()
    logging.info("CAPTCHA SENT AFTER START chat_id=%s user_id=%s message_id=%s", chat_id, user_id, captcha_message.message_id)
    await refresh_shared_notice(bot, chat_id)
    return True


async def handle_private_start(bot: Bot, user_id: int) -> bool:
    handled = False
    for chat_id, pending_user_id in list(pending):
        if pending_user_id != user_id:
            continue
        sent = await send_pending_private_captcha(bot, chat_id, user_id)
        handled = sent or handled
    return handled


async def process_queued_user(bot: Bot, chat_id: int, user: User) -> None:
    key = (chat_id, user.id)
    if user.id == BOT_ID:
        return
    if key in pending or key in processing_users:
        return
    processing_users.add(key)
    logging.info("JOIN DETECTED chat_id=%s user_id=%s username=%s", chat_id, user.id, user.username)
    try:
        if user.is_bot:
            await bot.ban_chat_member(chat_id, user.id)
            logging.info("KICKED chat_id=%s user_id=%s reason=bot", chat_id, user.id)
            return
        try:
            member = await bot.get_chat_member(chat_id, user.id)
            status = member_status(member.status)
            if status in {"administrator", "creator"}:
                logging.info("JOIN SKIPPED ADMIN chat_id=%s user_id=%s", chat_id, user.id)
                return
        except TelegramRetryAfter:
            raise
        except Exception as error:
            logging.warning("ADMIN STATUS CHECK FAILED chat_id=%s user_id=%s: %r", chat_id, user.id, error)
        try:
            await restrict_user(bot, chat_id, user.id)
        except Exception as error:
            logging.exception("RESTRICT ERROR chat_id=%s user_id=%s: %r", chat_id, user.id, error)
            await kick_user(bot, chat_id, user.id, "restrict_failed")
            return
        sent = await send_captcha(bot, chat_id, user)
        if not sent:
            await kick_user(bot, chat_id, user.id, "captcha_send_failed")
    except Exception as error:
        logging.exception("JOIN PROCESS ERROR chat_id=%s user_id=%s: %r", chat_id, user.id, error)
        clear_user(chat_id, user.id)
    finally:
        processing_users.discard(key)


async def join_worker(worker_id: int) -> None:
    while True:
        bot, chat_id, user = await join_queue.get()
        key = (chat_id, user.id)
        requeued = False
        try:
            await process_queued_user(bot, chat_id, user)
        except TelegramRetryAfter as error:
            delay = max(1, int(error.retry_after))
            logging.warning("TELEGRAM RATE LIMIT worker=%s retry_after=%s", worker_id, delay)
            await asyncio.sleep(delay)
            try:
                join_queue.put_nowait((bot, chat_id, user))
                requeued = True
            except asyncio.QueueFull:
                logging.error("JOIN QUEUE FULL AFTER RETRY chat_id=%s user_id=%s", chat_id, user.id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.exception("JOIN WORKER ERROR worker=%s chat_id=%s user_id=%s: %r", worker_id, chat_id, user.id, error)
        finally:
            if not requeued:
                queued_users.discard(key)
            join_queue.task_done()


async def start_workers() -> None:
    global raid_monitor_task
    if not worker_tasks:
        for worker_id in range(1, JOIN_WORKERS + 1):
            worker_tasks.append(asyncio.create_task(join_worker(worker_id)))
    if raid_monitor_task is None or raid_monitor_task.done():
        raid_monitor_task = asyncio.create_task(raid_monitor())
    logging.info("ANTI RAID ENGINE STARTED workers=%s", JOIN_WORKERS)


async def stop_workers() -> None:
    global raid_monitor_task
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    worker_tasks.clear()
    if raid_monitor_task is not None:
        raid_monitor_task.cancel()
        await asyncio.gather(raid_monitor_task, return_exceptions=True)
        raid_monitor_task = None


async def start_for_user(bot: Bot, chat_id: int, user: User) -> None:
    global raid_bot, raid_chat_id
    key = (chat_id, user.id)
    if user.id == BOT_ID or key in pending or key in processing_users or key in queued_users:
        return
    was_raid_mode = raid_mode
    raid_bot = bot
    raid_chat_id = chat_id
    register_join_spike()
    if worker_tasks and not was_raid_mode and raid_mode:
        await send_raid_started_alert(bot, chat_id)

    # В тестовом/встроенном режиме без запущенных воркеров сохраняем прежнее
    # немедленное поведение. В рабочем запуске main.py сначала вызывает
    # start_workers(), поэтому массовые входы идут через защищённую очередь.
    if not worker_tasks:
        await process_queued_user(bot, chat_id, user)
        return

    queued_users.add(key)
    try:
        join_queue.put_nowait((bot, chat_id, user))
        logging.info("JOIN QUEUED chat_id=%s user_id=%s queue=%s raid=%s", chat_id, user.id, join_queue.qsize(), raid_mode)
    except asyncio.QueueFull:
        queued_users.discard(key)
        logging.critical("JOIN QUEUE OVERFLOW chat_id=%s user_id=%s", chat_id, user.id)
        try:
            await restrict_user(bot, chat_id, user.id)
        except Exception:
            logging.exception("EMERGENCY RESTRICT FAILED chat_id=%s user_id=%s", chat_id, user.id)


async def handle_new_chat_members(bot: Bot, message: Message) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    for user in message.new_chat_members or []:
        await start_for_user(bot, message.chat.id, user)


async def handle_chat_member_update(bot: Bot, event: ChatMemberUpdated) -> None:
    user = event.new_chat_member.user
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    old_present = member_is_present(event.old_chat_member)
    new_present = member_is_present(event.new_chat_member)
    logging.info(
        "JOIN MEMBER UPDATE chat_id=%s user_id=%s %s(is_member=%s) -> %s(is_member=%s)",
        event.chat.id,
        user.id,
        old_status,
        old_present,
        new_status,
        new_present,
    )
    if user_just_left(event):
        clear_user(event.chat.id, user.id)
        return
    if user_just_joined(event):
        await start_for_user(bot, event.chat.id, user)


async def handle_callback(bot: Bot, callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user or not callback.data:
        return
    user_id = callback.from_user.id
    matches = [(chat_id, data) for (chat_id, pending_user_id), data in pending.items() if pending_user_id == user_id]
    if not matches:
        await callback.answer("Проверка уже завершена.", show_alert=True)
        return
    chat_id, data = matches[0]
    if not data:
        await callback.answer("Проверка уже завершена.", show_alert=True)
        return
    try:
        selected = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка проверки.", show_alert=True)
        return
    if selected != int(data["correct"]):
        await callback.answer("❌ Неверно. Попробуйте ещё раз.", show_alert=True)
        await kick_user(bot, chat_id, user_id, "wrong_answer")
        return
    await callback.answer("✅ Верно!")
    await pass_user(bot, chat_id, user_id)


async def pass_user(bot: Bot, chat_id: int, user_id: int) -> None:
    global raid_passed
    data = pending.get((chat_id, user_id))
    if not data:
        return
    if int(data.get("message_id", 0)):
        message_chat_id = int(data.get("message_chat_id", 0)) or chat_id
        await safe_delete_message(bot, message_chat_id, int(data["message_id"]))
    try:
        await unrestrict_user(bot, chat_id, user_id)
        logging.info("PASSED chat_id=%s user_id=%s", chat_id, user_id)
        stats.increment("captcha_passed", chat_id=chat_id)
        if raid_mode:
            raid_passed += 1
            save_raid_state()
    except Exception as error:
        logging.exception("UNRESTRICT ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)
    clear_user(chat_id, user_id)
    logging.info("HIDDEN CAPTCHA COMPLETE chat_id=%s user_id=%s", chat_id, user_id)


async def restore_pending(bot: Bot) -> None:
    load_raid_state()
    pending.clear()
    timeout_tasks.clear()
    now = time.time()
    restored = 0
    for row in load_state():
        try:
            chat_id = int(row["chat_id"])
            user_id = int(row["user_id"])
            message_id = int(row["message_id"])
            message_chat_id = int(row.get("message_chat_id", 0)) or chat_id
            correct = int(row["correct"])
            deadline = float(row["deadline"])
            delivery = str(row.get("delivery", "private"))
        except (KeyError, TypeError, ValueError):
            continue
        pending[(chat_id, user_id)] = {
            "message_id": message_id,
            "message_chat_id": message_chat_id,
            "correct": correct,
            "deadline": deadline,
            "delivery": delivery,
        }
        start_timeout(bot, chat_id, user_id)
        restored += 1
        if deadline <= now:
            logging.info("TIMEOUT RESTORED EXPIRED chat_id=%s user_id=%s", chat_id, user_id)
    save_state()
    logging.info("JOIN MANAGER RESTORED active_captchas=%s", restored)
