import asyncio
import json
import random
from pathlib import Path

from aiogram import Bot
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from utils import chunk_buttons, safe_delete_message


QUESTIONS_PATH = Path(__file__).resolve().parent / "captcha_questions.json"
CAPTCHA_TIMEOUT_SECONDS = 120

pending: dict[tuple[int, int], dict[str, int | asyncio.Task[None]]] = {}
passed_users: set[tuple[int, int]] = set()


def load_questions() -> list[dict[str, object]]:
    with QUESTIONS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


QUESTIONS = load_questions()


def has_passed(chat_id: int, user_id: int) -> bool:
    return (chat_id, user_id) in passed_users


def is_pending(chat_id: int, user_id: int) -> bool:
    return (chat_id, user_id) in pending


def make_question() -> tuple[dict[str, object], InlineKeyboardMarkup, int]:
    question = random.choice(QUESTIONS)
    answers = list(enumerate(question["answers"]))
    random.shuffle(answers)
    correct = next(index for index, (old_index, _) in enumerate(answers) if old_index == question["correct"])
    buttons = [
        InlineKeyboardButton(text=str(answer), callback_data=f"captcha:{index}")
        for index, (_, answer) in enumerate(answers)
    ]
    return question, InlineKeyboardMarkup(inline_keyboard=chunk_buttons(buttons, 1)), correct


def text_for(question: dict[str, object]) -> str:
    return (
        "🛡 Police | Проверка нового участника\n\n"
        "Для защиты группы ответьте на вопрос.\n\n"
        "🟨 ВОПРОС\n\n"
        f"{question['question']}\n\n"
        "Выберите правильный ответ."
    )


async def mute(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
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
            ),
        )
    except Exception as error:
        print("Ошибка ограничения нового участника:", repr(error))


async def unmute(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
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
            ),
        )
    except Exception as error:
        print("Ошибка снятия ограничений после капчи:", repr(error))


async def start(bot: Bot, message: Message, user: User) -> None:
    await start_for_chat(bot, message.chat.id, user)


async def start_for_chat(bot: Bot, chat_id: int, user: User) -> None:
    if is_pending(chat_id, user.id) or has_passed(chat_id, user.id):
        return
    await mute(bot, chat_id, user.id)
    await send_new_question(bot, chat_id, user.id)


async def send_new_question(bot: Bot, chat_id: int, user_id: int) -> None:
    question, keyboard, correct = make_question()
    try:
        captcha_message = await bot.send_message(chat_id, text_for(question), reply_markup=keyboard)
    except Exception as error:
        print("Ошибка отправки капчи:", repr(error))
        return
    timeout_task = asyncio.create_task(timeout(bot, chat_id, user_id))
    pending[(chat_id, user_id)] = {
        "message_id": captcha_message.message_id,
        "correct": correct,
        "timeout_task": timeout_task,
    }


async def replace_question(bot: Bot, chat_id: int, user_id: int) -> None:
    data = pending.get((chat_id, user_id))
    if not data:
        return

    question, keyboard, correct = make_question()
    message_id = int(data["message_id"])
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text_for(question),
            reply_markup=keyboard,
        )
    except Exception as error:
        print("Ошибка обновления капчи:", repr(error))
        await safe_delete_message(bot, chat_id, message_id)
        try:
            captcha_message = await bot.send_message(chat_id, text_for(question), reply_markup=keyboard)
            data["message_id"] = captcha_message.message_id
        except Exception as send_error:
            print("Ошибка повторной отправки капчи:", repr(send_error))
            return
    data["correct"] = correct


async def pass_user(bot: Bot, chat_id: int, user_id: int) -> None:
    data = pending.pop((chat_id, user_id), None)
    if data:
        timeout_task = data.get("timeout_task")
        if isinstance(timeout_task, asyncio.Task):
            timeout_task.cancel()
        await safe_delete_message(bot, chat_id, int(data["message_id"]))
    passed_users.add((chat_id, user_id))
    await unmute(bot, chat_id, user_id)
    try:
        await bot.send_message(
            chat_id,
            "🛡 Добро пожаловать в Группу!\n\n"
            "Пусть Евгений будет на вашей стороне! ⚔️\n\n"
            "Не флудите, не спамьте и приятного общения! 🍻",
        )
    except Exception as error:
        print("Ошибка отправки приветствия:", repr(error))


async def timeout(bot: Bot, chat_id: int, user_id: int) -> None:
    await asyncio.sleep(CAPTCHA_TIMEOUT_SECONDS)
    data = pending.get((chat_id, user_id))
    if not data:
        return
    pending.pop((chat_id, user_id), None)
    await safe_delete_message(bot, chat_id, int(data["message_id"]))
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
    except Exception as error:
        print("Ошибка удаления пользователя после капчи:", repr(error))
