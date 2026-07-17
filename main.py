import asyncio
import json
import logging
import os
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web
from dotenv import load_dotenv

import join_manager
import conversation
import jokes
import luck
import moderation
import predictions
import stories
import toasts
import stats
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


def looks_like_ad(message: Message, *, allow_plain_links: bool) -> bool:
    """Проверяет сообщение на рекламу, не запрещая обычные ссылки доверенным участникам.

    Участник считается доверенным, если он не находится в активной капче.
    Поэтому старые участники группы и пользователи, успешно прошедшие капчу,
    могут отправлять обычные ссылки. Рекламные формулировки, мошеннические
    предложения и массовые призывы всё равно блокируются для всех.
    """
    text = message.text or message.caption or ""
    lowered = " ".join(text.lower().split())

    has_clickable_link = False
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        entity_type = getattr(entity.type, "value", entity.type)
        if entity_type in {"url", "text_link"}:
            has_clickable_link = True
            break

    has_text_link = any(
        marker in lowered
        for marker in ("http://", "https://", "t.me/", "telegram.me/", "www.")
    )

    # Само наличие ссылки не считается рекламой для доверенного участника.
    if (has_clickable_link or has_text_link) and not allow_plain_links:
        return True

    ad_markers = [
        "заработок", "заработать", "подработка", "доход без",
        "инвест", "крипт", "казино", "ставки", "букмекер",
        "розыгрыш", "промокод", "пиши в личку", "пишите в личку",
        "переходи по ссылке", "подписывайся", "подпишись на канал",
        "ищу людей", "набор в команду", "удаленная работа",
    ]
    return any(marker in lowered for marker in ad_markers)


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
    await join_manager.handle_new_chat_members(bot, message)


@dp.chat_member()
async def chat_member_update(event) -> None:
    await join_manager.handle_chat_member_update(bot, event)


@dp.callback_query(F.data.startswith("captcha:"))
async def captcha_callback(callback) -> None:
    await join_manager.handle_callback(bot, callback)


@dp.message(Command("start"))
async def start_command(message: Message) -> None:
    if not message.from_user:
        return
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type != "private":
        return
    handled = await join_manager.handle_private_start(bot, message.from_user.id)
    if not handled:
        await safe_answer(message, "Police online.")

@dp.message(Command("анекдот"))
async def anecdote(message: Message) -> None:
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
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


def is_raid_controller(message: Message) -> bool:
    if not message.from_user:
        return False
    username = (message.from_user.username or "").lower()
    return username in {OWNER_USERNAME, YURA_USERNAME}


async def require_raid_controller(message: Message) -> bool:
    if is_raid_controller(message):
        return True
    await safe_answer(message, "⛔ Эта команда доступна только Michel и Юре.")
    return False


@dp.message(Command("осадавкл"))
async def raid_on_command(message: Message) -> None:
    if not await require_raid_controller(message):
        return
    join_manager.set_raid_mode(True, forced=True)
    await safe_answer(message, "🛡 Режим ОСАДА включён вручную. Новые участники идут через защищённую очередь.")


@dp.message(Command("осадавыкл"))
async def raid_off_command(message: Message) -> None:
    if not await require_raid_controller(message):
        return
    join_manager.set_raid_mode(False)
    await safe_answer(message, "✅ Режим ОСАДА выключен. Бот вернулся в обычный режим.")


@dp.message(Command("осадастатус"))
@dp.message(Command("нагрузка"))
async def raid_status_command(message: Message) -> None:
    if not await require_raid_controller(message):
        return
    await safe_answer(message, join_manager.raid_status_text())


@dp.message(Command("история"))
async def story_command(message: Message) -> None:
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
        return
    if not stories.can_use(message.from_user.id):
        await safe_answer(message, stories.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, stories.get_story(message.from_user.id))


@dp.message(Command("тост"))
async def toast_command(message: Message) -> None:
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
        return
    if not toasts.can_use(message.from_user.id):
        await safe_answer(message, toasts.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, toasts.get_toast(message.from_user.id))


@dp.message(Command("предсказание"))
async def prediction_command(message: Message) -> None:
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
        return
    if not predictions.can_use(message.from_user.id):
        await safe_answer(message, predictions.cooldown_text(message.from_user.id))
        return
    await safe_answer(message, predictions.get_prediction(message.from_user.id))


@dp.message(Command("police", ignore_case=True))
async def police_command(message: Message) -> None:
    """Показывает статистику только администраторам группы."""
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
        return

    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type not in {"group", "supergroup"}:
        await safe_answer(message, "⛔ Команда /police доступна только администраторам группы.")
        return

    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        status = getattr(member.status, "value", member.status)
    except Exception as error:
        logging.exception("Не удалось проверить права для /police: %r", error)
        await safe_answer(message, "⚠️ Не удалось проверить права администратора. Попробуйте ещё раз.")
        return

    if status not in {"administrator", "creator"}:
        # Для обычных участников команда недоступна.
        await safe_answer(message, "⛔ Команда /police доступна только администраторам группы.")
        return

    stats.register_chat(message.chat.id)
    await safe_answer(message, stats.police_text())


@dp.message(Command("фарт"))
async def luck_command(message: Message) -> None:
    if not message.from_user:
        return
    if join_manager.is_pending(message.chat.id, message.from_user.id):
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
    pending_captcha = join_manager.is_pending(message.chat.id, message.from_user.id)

    # Старые участники и прошедшие капчу могут публиковать обычные ссылки.
    # Рекламные формулировки по-прежнему удаляются у всех.
    if looks_like_ad(message, allow_plain_links=not pending_captcha):
        await safe_delete_message(bot, message.chat.id, message.message_id)
        stats.increment("ads_removed", chat_id=message.chat.id)
        return

    if pending_captcha:
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


async def daily_stats_worker() -> None:
    """В 23:59 по времени Молдовы публикует суточную сводку один раз."""
    from datetime import datetime

    while True:
        try:
            now = datetime.now(stats.MOLDOVA_TZ)
            if now.hour == 23 and now.minute == 59 and not stats.report_already_sent():
                text = stats.daily_report_text()
                sent = False
                for chat_id in stats.chat_ids():
                    try:
                        await bot.send_message(chat_id, text)
                        sent = True
                    except Exception as error:
                        logging.exception(
                            "Не удалось отправить суточную сводку chat_id=%s: %r",
                            chat_id,
                            error,
                        )
                if sent:
                    stats.mark_report_sent()
            # snapshot() также сбрасывает счётчики после наступления нового дня.
            stats.snapshot()
        except Exception as error:
            logging.exception("Ошибка суточной статистики: %r", error)
        await asyncio.sleep(20)


async def main() -> None:
    global BOT_ID
    global BOT_NAME
    global BOT_USERNAME

    health_runner = await start_health_server()
    daily_stats_task = asyncio.create_task(daily_stats_worker())
    try:
        me = await bot.get_me()
        BOT_ID = me.id
        BOT_USERNAME = me.username
        BOT_NAME = me.full_name
        join_manager.set_bot_id(BOT_ID)
        await join_manager.restore_pending(bot)
        await join_manager.start_workers()
        await bot.delete_webhook(drop_pending_updates=False)
        # Явно запрашиваем chat_member: без него Telegram может присылать только
        # обычные сообщения, и входы новых участников останутся незамеченными.
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
        )
    finally:
        daily_stats_task.cancel()
        try:
            await daily_stats_task
        except asyncio.CancelledError:
            pass
        await join_manager.stop_workers()
        await health_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
