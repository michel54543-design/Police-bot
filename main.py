import asyncio
import json
import logging
import os
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, User
from aiohttp import web
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


def looks_like_ad(message: Message) -> bool:
    """Проверяет текст, подпись и Telegram-сущности на явную рекламу.

    Проверка выполняется до разговорного режима, поэтому Police может молчать
    в обычном чате, но продолжает удалять рекламные сообщения.
    """
    text = message.text or message.caption or ""
    lowered = " ".join(text.lower().split())

    # Любая кликабельная внешняя ссылка считается подозрительной рекламой.
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        entity_type = getattr(entity.type, "value", entity.type)
        if entity_type in {"url", "text_link"}:
            return True

    markers = [
        "http://", "https://", "t.me/", "telegram.me/", "www.",
        "заработок", "заработать", "подработка", "доход без",
        "инвест", "крипт", "казино", "ставки", "букмекер",
        "розыгрыш", "промокод", "пиши в личку", "пишите в личку",
        "переходи по ссылке", "подписывайся", "подпишись на канал",
        "ищу людей", "набор в команду", "удаленная работа",
    ]
    return any(marker in lowered for marker in markers)


def member_status(value: object) -> str:
    return str(getattr(value, "value", value))


def user_just_joined(event: ChatMemberUpdated) -> bool:
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    return old_status in {"left", "kicked"} and new_status in {
        "member", "restricted", "administrator", "creator"
    }


def user_just_left(event: ChatMemberUpdated) -> bool:
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    return old_status in {"member", "restricted", "administrator", "creator"} and new_status in {"left", "kicked"}


async def process_new_user(message: Message | None, chat_id: int, user: User) -> None:
    """Запускает защиту нового участника независимо от типа join-события."""
    if user.id == BOT_ID:
        return
    if captcha.is_pending(chat_id, user.id) or captcha.has_passed(chat_id, user.id):
        return

    if user.is_bot:
        try:
            await bot.ban_chat_member(chat_id, user.id)
        except Exception as error:
            logging.exception("Ошибка бана вошедшего бота: %r", error)
        return

    try:
        if message is not None:
            await captcha.start(bot, message, user)
        else:
            await captcha.start_for_chat(bot, chat_id, user)
        logging.info("Капча выдана новому участнику chat_id=%s user_id=%s", chat_id, user.id)
    except Exception as error:
        logging.exception("Не удалось запустить капчу chat_id=%s user_id=%s: %r", chat_id, user.id, error)


def is_owner(message: Message) -> bool:
    if not message.from_user:
        return False
    return (message.from_user.username or "").lower() == OWNER_USERNAME


def is_yura(message: Message) -> bool:
    if not message.from_user:
        return False
    return (message.from_user.username or "").lower() == YURA_USERNAME


def addressed_to_bot(message: Message) -> bool:
    """Возвращает True только при явном обращении к Police.

    Обычное упоминание слова в середине разговора не считается обращением.
    Модерация рекламы и нарушений выполняется до этой проверки.
    """
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type == "private":
        return True

    reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if BOT_ID is not None and reply_user is not None and reply_user.id == BOT_ID:
        return True

    raw_text = message.text or message.caption or ""
    text = " ".join(raw_text.lower().strip().split())
    if not text:
        return False

    if BOT_USERNAME and re.search(
        rf"(?<![\w@])@{re.escape(BOT_USERNAME.lower())}(?!\w)", text
    ):
        return True

    # Явное обращение в начале: «Police», «Police, привет», «Police ты здесь?»
    if re.match(r"^police(?:$|[\s,.:;!?—-])", text):
        return True

    # Явное обращение в конце: «ответь, Police»
    if re.search(r"(?:^|[\s,.:;!?—-])police[.!?]*$", text):
        return True

    # Слово «бот» учитывается только как отдельное обращение, а не внутри фразы.
    if re.match(r"^бот(?:$|[,.:;!?—-])", text) or re.search(
        r"(?:^|[,.:;!?—-]\s*)бот[.!?]*$", text
    ):
        return True

    if BOT_NAME:
        name = " ".join(BOT_NAME.lower().strip().split())
        if text == name or text.startswith(name + ",") or text.startswith(name + "!") or text.startswith(name + "?"):
            return True

    return False


def owner_reply() -> str:
    return choose("owner", OWNER_MESSAGES)


def yura_reply() -> str:
    return choose("yura", YURA_MESSAGES)


@dp.message(F.new_chat_members)
async def new_members(message: Message) -> None:
    await safe_delete_message(bot, message.chat.id, message.message_id)
    for user in message.new_chat_members:
        await process_new_user(message, message.chat.id, user)


@dp.chat_member()
async def chat_member_update(event: ChatMemberUpdated) -> None:
    """Надёжно обрабатывает вход и выход участника через chat_member update."""
    user = event.new_chat_member.user
    old_status = member_status(event.old_chat_member.status)
    new_status = member_status(event.new_chat_member.status)
    logging.info(
        "chat_member update chat_id=%s user_id=%s %s -> %s",
        event.chat.id, user.id, old_status, new_status,
    )

    if user_just_left(event):
        captcha.forget_user(event.chat.id, user.id)
        return

    if user_just_joined(event):
        await process_new_user(None, event.chat.id, user)


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

    text = message.text or message.caption or ""
    if looks_like_ad(message):
        await safe_delete_message(bot, message.chat.id, message.message_id)
        return

    if captcha.is_pending(message.chat.id, message.from_user.id):
        await safe_delete_message(bot, message.chat.id, message.message_id)
        return

    addressed = addressed_to_bot(message)

    if await moderation.handle_bad_language(bot, message, addressed):
        return

    if not addressed:
        return

    if is_owner(message) and addressed:
        await human_pause(message)
        await message.answer(owner_reply())
        return

    if is_yura(message) and addressed:
        await human_pause(message)
        await message.reply(yura_reply())
        return

    if text and conversation.looks_rude(text) and conversation.can_reply_rude(message.from_user.id):
        await human_pause(message)
        rude_text = conversation.rude_reply(message.from_user.id)
        if is_yura(message):
            rude_text = f"Юра, {rude_text}"
        await message.reply(rude_text)
        return

    if message.text:
        conversation.remember(message.from_user.id, message.text)
        reply = conversation.reply_for(message.from_user.id, message.text, addressed=True)
        if reply:
            await human_pause(message)
            await message.answer(style(reply))


async def health(request: web.Request) -> web.Response:
    return web.Response(text="Police Bot is running")


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server started on port %s", port)
    return runner


async def main() -> None:
    global BOT_ID
    global BOT_NAME
    global BOT_USERNAME

    health_runner = await start_health_server()
    try:
        me = await bot.get_me()
        BOT_ID = me.id
        BOT_USERNAME = me.username
        BOT_NAME = me.full_name
        await bot.delete_webhook(drop_pending_updates=True)
        # Явно запрашиваем chat_member: без него Telegram может присылать только
        # обычные сообщения, и входы новых участников останутся незамеченными.
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        )
    finally:
        await health_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
