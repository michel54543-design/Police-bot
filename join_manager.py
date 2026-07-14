import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from aiogram import Bot
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


BASE_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = BASE_DIR / "captcha_questions.json"
STATE_PATH = BASE_DIR / "join_state.json"
CAPTCHA_TIMEOUT_SECONDS = 120

pending: dict[tuple[int, int], dict[str, Any]] = {}
processing_users: set[tuple[int, int]] = set()
timeout_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
BOT_ID: int | None = None


def load_questions() -> list[dict[str, Any]]:
    with QUESTIONS_PATH.open("r", encoding="utf-8") as file:
        questions = json.load(file)
    if not isinstance(questions, list) or not questions:
        raise ValueError("captcha_questions.json must contain a non-empty list")
    return questions


QUESTIONS = load_questions()


def set_bot_id(bot_id: int | None) -> None:
    global BOT_ID
    BOT_ID = bot_id


def member_status(value: object) -> str:
    return str(getattr(value, "value", value))


def user_just_joined(event: ChatMemberUpdated) -> bool:
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    return old_status in {"left", "kicked"} and new_status in {
        "member",
        "restricted",
        "administrator",
        "creator",
    }


def user_just_left(event: ChatMemberUpdated) -> bool:
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    return old_status in {"member", "restricted", "administrator", "creator"} and new_status in {
        "left",
        "kicked",
    }


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
                "message_id": int(data["message_id"]),
                "correct": int(data["correct"]),
                "deadline": float(data["deadline"]),
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
    data = pending.get((chat_id, user_id))
    if data is not None:
        await safe_delete_message(bot, chat_id, int(data["message_id"]))
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        logging.info("KICKED chat_id=%s user_id=%s reason=%s", chat_id, user_id, reason)
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


async def send_captcha(bot: Bot, chat_id: int, user_id: int) -> bool:
    question, keyboard, correct = make_question()
    try:
        captcha_message = await bot.send_message(chat_id, captcha_text(question), reply_markup=keyboard)
    except Exception as error:
        logging.exception("CAPTCHA SEND ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)
        return False
    pending[(chat_id, user_id)] = {
        "message_id": captcha_message.message_id,
        "correct": correct,
        "deadline": time.time() + CAPTCHA_TIMEOUT_SECONDS,
    }
    save_state()
    start_timeout(bot, chat_id, user_id)
    logging.info("CAPTCHA SENT chat_id=%s user_id=%s message_id=%s", chat_id, user_id, captcha_message.message_id)
    return True


async def start_for_user(bot: Bot, chat_id: int, user: User) -> None:
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
            await restrict_user(bot, chat_id, user.id)
        except Exception as error:
            logging.exception("RESTRICT ERROR chat_id=%s user_id=%s: %r", chat_id, user.id, error)
            await kick_user(bot, chat_id, user.id, "restrict_failed")
            return
        sent = await send_captcha(bot, chat_id, user.id)
        if not sent:
            await kick_user(bot, chat_id, user.id, "captcha_send_failed")
    except Exception as error:
        logging.exception("JOIN PROCESS ERROR chat_id=%s user_id=%s: %r", chat_id, user.id, error)
        clear_user(chat_id, user.id)
    finally:
        processing_users.discard(key)


async def handle_new_chat_members(bot: Bot, message: Message) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    for user in message.new_chat_members or []:
        await start_for_user(bot, message.chat.id, user)


async def handle_chat_member_update(bot: Bot, event: ChatMemberUpdated) -> None:
    user = event.new_chat_member.user
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    logging.info(
        "JOIN MEMBER UPDATE chat_id=%s user_id=%s %s -> %s",
        event.chat.id,
        user.id,
        old_status,
        new_status,
    )
    if user_just_left(event):
        clear_user(event.chat.id, user.id)
        return
    if user_just_joined(event):
        await start_for_user(bot, event.chat.id, user)


async def handle_callback(bot: Bot, callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user or not callback.data:
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    data = pending.get((chat_id, user_id))
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
    data = pending.get((chat_id, user_id))
    if not data:
        return
    await safe_delete_message(bot, chat_id, int(data["message_id"]))
    try:
        await unrestrict_user(bot, chat_id, user_id)
        logging.info("PASSED chat_id=%s user_id=%s", chat_id, user_id)
    except Exception as error:
        logging.exception("UNRESTRICT ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)
    clear_user(chat_id, user_id)
    try:
        await bot.send_message(
            chat_id,
            "🛡 Добро пожаловать в Группу!\n\n"
            "Пусть Евгений будет на вашей стороне! ⚔️\n\n"
            "Не флудите, не спамьте и приятного общения! 🍻",
        )
    except Exception as error:
        logging.exception("WELCOME SEND ERROR chat_id=%s user_id=%s: %r", chat_id, user_id, error)


async def restore_pending(bot: Bot) -> None:
    pending.clear()
    timeout_tasks.clear()
    now = time.time()
    restored = 0
    for row in load_state():
        try:
            chat_id = int(row["chat_id"])
            user_id = int(row["user_id"])
            message_id = int(row["message_id"])
            correct = int(row["correct"])
            deadline = float(row["deadline"])
        except (KeyError, TypeError, ValueError):
            continue
        pending[(chat_id, user_id)] = {
            "message_id": message_id,
            "correct": correct,
            "deadline": deadline,
        }
        start_timeout(bot, chat_id, user_id)
        restored += 1
        if deadline <= now:
            logging.info("TIMEOUT RESTORED EXPIRED chat_id=%s user_id=%s", chat_id, user_id)
    save_state()
    logging.info("JOIN MANAGER RESTORED active_captchas=%s", restored)
