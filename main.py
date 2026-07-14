import asyncio
import json
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dotenv import load_dotenv

import captcha
import conversation
import jokes
import luck
import moderation
import predictions
import stories
import toasts
from personality import style
from reply_selector import choose
from utils import human_pause, safe_delete_message


BASE_DIR = Path(__file__).resolve().parent

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Добавьте BOT_TOKEN в переменные окружения Render")

logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()

BOT_ID: int | None = None
BOT_USERNAME: str | None = None
BOT_NAME: str | None = None
OWNER_USERNAME = "michel54543"
YURA_USERNAME = "darkboogimen"


def load_list(filename: str) -> list[str]:
    with (BASE_DIR / filename).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{filename} должен содержать список строк")
    return [str(item).strip() for item in data if str(item).strip()]


OWNER_MESSAGES = load_list("owner_replies.json")
YURA_MESSAGES = load_list("yura_replies.json")


def looks_like_ad(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "http://",
        "https://",
        "t.me/",
        "telegram.me/",
        "заработок",
        "инвест",
        "казино",
        "ставки",
    ]
    return any(marker in lowered for marker in markers)


def is_owner(message: Message) -> bool:
    if not message.from_user:
        return False
    return (message.from_user.username or "").lower() == OWNER_USERNAME


def is_yura(message: Message) -> bool:
    if not message.from_user:
        return False
    return (message.from_user.username or "").lower() == YURA_USERNAME


def addressed_to_bot(message: Message) -> bool:
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type == "private":
        return True

    reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if BOT_ID is not None and reply_user is not None and reply_user.id == BOT_ID:
        return True

    text = (message.text or message.caption or "").lower()
    if BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text:
        return True
    if BOT_NAME and BOT_NAME.lower() in text:
        return True

    return "police" in text or "бот" in text


def owner_reply() -> str:
    return choose("owner", OWNER_MESSAGES)


def yura_reply() -> str:
    return choose("yura", YURA_MESSAGES)


@dp.message(F.new_chat_members)
async def new_members(message: Message) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    for user in message.new_chat_members:
        if user.is_bot:
            try:
                await bot.ban_chat_member(message.chat.id, user.id)
            except Exception as error:
                print("Ошибка бана бота:", repr(error))
            continue
        await captcha.start(bot, message, user)


@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_callback(callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user:
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    data = captcha.pending.get((chat_id, user_id))
    if not data:
        await callback.answer("Проверка уже завершена.", show_alert=True)
        return

    selected = int(callback.data.split(":", 1)[1])
    if selected == data["correct"]:
        await callback.answer("✅ Верно!")
        await captcha.pass_user(bot, chat_id, user_id)
        return

    await callback.answer("❌ Неверно. Попробуйте ещё раз.", show_alert=True)
    await captcha.replace_question(bot, chat_id, user_id)


@dp.message(Command("анекдот"))
async def anecdote(message: Message) -> None:
    if not message.from_user:
        return
    if captcha.is_pending(message.chat.id, message.from_user.id):
        return
    if not jokes.can_use(message.from_user.id):
        await message.answer(jokes.cooldown_text(message.from_user.id))
        return
    await message.answer(jokes.get_joke(message.from_user.id))


async def safe_answer(message: Message, text: str) -> None:
    try:
        await message.answer(text)
    except Exception as error:
        print("Ошибка отправки ответа команды:", repr(error))


@dp.message(Command("история"))
async def story_command(message: Message) -> None:
    if not message.from_user:
        return
    if captcha.is_pending(message.chat.id, message.from_user.id):
        return
    if not stories.can_use(message.from_user.id):
        await safe_answer(message, stories.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, stories.get_story(message.from_user.id))


@dp.message(Command("тост"))
async def toast_command(message: Message) -> None:
    if not message.from_user:
        return
    if captcha.is_pending(message.chat.id, message.from_user.id):
        return
    if not toasts.can_use(message.from_user.id):
        await safe_answer(message, toasts.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, toasts.get_toast(message.from_user.id))


@dp.message(Command("предсказание"))
async def prediction_command(message: Message) -> None:
    if not message.from_user:
        return
    if captcha.is_pending(message.chat.id, message.from_user.id):
        return
    if not predictions.can_use(message.from_user.id):
        await safe_answer(message, predictions.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, predictions.get_prediction(message.from_user.id))


@dp.message(Command("фарт"))
async def luck_command(message: Message) -> None:
    if not message.from_user:
        return
    if captcha.is_pending(message.chat.id, message.from_user.id):
        return
    if not luck.can_use(message.from_user.id):
        await safe_answer(message, luck.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, luck.get_luck(message.from_user.id))


@dp.message()
async def all_messages(message: Message) -> None:
    if not message.from_user:
        return

    if captcha.is_pending(message.chat.id, message.from_user.id):
        await safe_delete_message(bot, message.chat.id, message.message_id)
        return

    text = message.text or message.caption or ""
    if text and looks_like_ad(text):
        await safe_delete_message(bot, message.chat.id, message.message_id)
        return

    addressed = addressed_to_bot(message)

    if await moderation.handle_bad_language(bot, message, addressed):
        return

    if is_owner(message) and addressed:
        await human_pause(message)
        await message.answer(owner_reply())
        return

    if is_yura(message) and addressed:
        await human_pause(message)
        await message.reply(yura_reply())
        return

    if text and addressed and conversation.looks_rude(text) and conversation.can_reply_rude(message.from_user.id):
        await human_pause(message)
        rude_text = conversation.rude_reply(message.from_user.id)
        if is_yura(message):
            rude_text = f"Юра, {rude_text}"
        await message.reply(rude_text)
        return

    if message.text:
        conversation.remember(message.from_user.id, message.text)
        reply = conversation.reply_for(message.from_user.id, message.text, addressed=addressed)
        if reply:
            await human_pause(message)
            await message.answer(style(reply))


async def main() -> None:
    global BOT_ID
    global BOT_NAME
    global BOT_USERNAME

    web_runner = await start_web_server()
    try:
        me = await bot.get_me()
        BOT_ID = me.id
        BOT_USERNAME = me.username
        BOT_NAME = me.full_name

        # Remove an old webhook before starting long polling.
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
