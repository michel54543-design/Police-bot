import asyncio
import json
import logging
import os
import re
import threading
from contextlib import suppress
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from dotenv import load_dotenv

import auto_news
import attack_alerts
import challenges
import conversation
import image_job_ad_filter
import join_manager
import jokes
import luck
import moderation
import predictions
import playful_mode
import riddles
import stats
import stories
import toasts
from utils import safe_delete_message

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("police.main")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

BASE_DIR = Path(__file__).resolve().parent
OWNER_USERNAME = "michel54543"
YURA_USERNAME = "darkboogimen"

with (BASE_DIR / "owner_replies.json").open("r", encoding="utf-8") as f:
    OWNER_REPLIES = json.load(f)
with (BASE_DIR / "yura_replies.json").open("r", encoding="utf-8") as f:
    YURA_REPLIES = json.load(f)

BOT_ID = 0
BOT_USERNAME = ""
MOLDOVA_TZ = ZoneInfo("Europe/Chisinau")

AD_PHRASES = (
    "заработок без вложений", "доход в день", "работа на дому", "работа из дома",
    "пишите менеджеру", "ставки на спорт", "казино", "инвестиции с гарантией",
    "крипто доход", "легкие деньги", "лёгкие деньги", "вакансия удаленно", "вакансия удалённо",
)
AD_DOMAINS = (
    "t.me/", "telegram.me/", "wa.me/", "instagram.com/", "facebook.com/",
    "vk.com/", "discord.gg/", "bit.ly/", "tinyurl.com/",
)


def _entity_has_link(message: Message) -> tuple[bool, bool]:
    has_clickable_link = False
    has_text_link = False
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        etype = getattr(entity.type, "value", str(entity.type))
        if etype == "url":
            has_clickable_link = True
        elif etype == "text_link":
            has_text_link = True
    return has_clickable_link, has_text_link


def looks_like_ad(message: Message, *, allow_plain_links: bool) -> bool:
    text = (message.text or message.caption or "").lower()
    has_clickable_link, has_text_link = _entity_has_link(message)

    if (has_clickable_link or has_text_link) and not allow_plain_links:
        return True
    if any(domain in text for domain in AD_DOMAINS) and not allow_plain_links:
        return True
    # Рекламные формулировки блокируются даже у участников, прошедших капчу.
    if any(phrase in text for phrase in AD_PHRASES):
        return True
    return False


def is_bot_addressed(message: Message) -> bool:
    text = (message.text or message.caption or "").lower()
    if BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text:
        return True
    if re.search(r"(?:^|\s)(?:police(?:\s+bot)?|полис|полицейский|бот)(?:\s|$|[,.!?;:])", text):
        return True
    reply = message.reply_to_message
    return bool(reply and reply.from_user and reply.from_user.id == BOT_ID)


def health_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *_args):
            return

    port = int(os.getenv("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        status = getattr(member.status, "value", str(member.status))
        return status in {"administrator", "creator"}
    except Exception:
        return False


async def daily_report_worker(bot: Bot) -> None:
    """Надёжно отправляет суточную сводку один раз в 23:59 по Молдове."""
    while True:
        now = datetime.now(MOLDOVA_TZ)
        if now.hour == 23 and now.minute == 59 and not stats.report_already_sent():
            delivered = False
            report = stats.daily_report_text()
            for chat_id in stats.chat_ids():
                try:
                    await bot.send_message(chat_id, report)
                    delivered = True
                except Exception as error:
                    logger.exception("DAILY REPORT SEND ERROR chat_id=%s: %r", chat_id, error)
            if delivered:
                stats.mark_report_sent()
                logger.info("DAILY REPORT SENT date=%s", now.date().isoformat())
        await asyncio.sleep(10)


async def send_command_result(message: Message, module, getter_name: str) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    if not module.can_use(uid):
        await message.answer(module.cooldown_text(uid))
        return
    await message.answer(getattr(module, getter_name)(uid))


def register_handlers(dp: Dispatcher, bot: Bot) -> None:
    @dp.message(CommandStart())
    async def start_handler(message: Message) -> None:
        if message.chat.type == ChatType.PRIVATE and message.from_user:
            handled = await join_manager.handle_private_start(bot, message.from_user.id)
            if handled:
                return
        await message.answer("🛡 Police Bot на связи.")

    @dp.message(Command("анекдот"))
    async def joke_handler(message: Message) -> None:
        await send_command_result(message, jokes, "get_joke")

    @dp.message(Command("история"))
    async def story_handler(message: Message) -> None:
        await send_command_result(message, stories, "get_story")

    @dp.message(Command("тост"))
    async def toast_handler(message: Message) -> None:
        await send_command_result(message, toasts, "get_toast")

    @dp.message(Command("предсказание"))
    async def prediction_handler(message: Message) -> None:
        await send_command_result(message, predictions, "get_prediction")

    @dp.message(Command("фарт"))
    async def luck_handler(message: Message) -> None:
        await send_command_result(message, luck, "get_luck")

    @dp.message(Command("загадка"))
    async def riddle_handler(message: Message) -> None:
        if not message.from_user:
            return
        parts = (message.text or "").split(maxsplit=1)
        requested_category = parts[1] if len(parts) > 1 else None
        if requested_category and not riddles.normalize_category(requested_category):
            await message.answer("Категории: обычная, смешная, логическая, с_подвохом.")
            return
        await message.answer(riddles.new_riddle(
            message.chat.id,
            message.from_user.id,
            requested_category,
        ))

    @dp.message(Command("испытание"))
    async def challenge_handler(message: Message) -> None:
        await message.answer(challenges.get_challenge())

    @dp.message(Command("атакивкл"))
    async def attacks_on_handler(message: Message) -> None:
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer("Эту команду нужно отправить в общей группе.")
            return
        stats.register_chat(message.chat.id)
        logger.info("ATTACK ALERTS ENABLED chat_id=%s", message.chat.id)
        await message.answer(await attack_alerts.schedule_status_text())

    @dp.message(Command("атакистатус"))
    async def attacks_status_handler(message: Message) -> None:
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            stats.register_chat(message.chat.id)
        await message.answer(await attack_alerts.schedule_status_text())

    @dp.message(Command("осадавкл"))
    async def raid_on(message: Message) -> None:
        if not message.from_user or (message.from_user.username or "").lower() not in {OWNER_USERNAME, YURA_USERNAME}:
            return
        join_manager.set_raid_mode(True, forced=True)
        await message.answer(join_manager.raid_status_text())

    @dp.message(Command("осадавыкл"))
    async def raid_off(message: Message) -> None:
        if not message.from_user or (message.from_user.username or "").lower() not in {OWNER_USERNAME, YURA_USERNAME}:
            return
        join_manager.set_raid_mode(False)
        await message.answer(join_manager.raid_status_text())

    @dp.message(Command("осадастатус", "нагрузка"))
    async def raid_status(message: Message) -> None:
        if not message.from_user or (message.from_user.username or "").lower() not in {OWNER_USERNAME, YURA_USERNAME}:
            return
        await message.answer(join_manager.raid_status_text())

    @dp.callback_query(F.data.startswith("captcha:"))
    async def captcha_callback(callback: CallbackQuery) -> None:
        await join_manager.handle_callback(bot, callback)

    @dp.callback_query(F.data == "verify:open")
    async def verify_callback(callback: CallbackQuery) -> None:
        await join_manager.handle_verify_button(bot, callback)

    @dp.chat_member()
    async def chat_member_handler(event: ChatMemberUpdated) -> None:
        await join_manager.handle_chat_member_update(bot, event)

    @dp.message(F.new_chat_members)
    async def new_members_handler(message: Message) -> None:
        await join_manager.handle_new_chat_members(bot, message)

    @dp.message()
    async def general_handler(message: Message) -> None:
        if not message.from_user:
            return
        if message.chat.type == ChatType.PRIVATE:
            username = (message.from_user.username or "").lower()
            text = (message.text or message.caption or "").strip()
            lowered = text.lower().replace("ё", "е")
            if username == OWNER_USERNAME and lowered.startswith("начать шутить"):
                target = playful_mode.parse_target(text)
                if not target:
                    await message.answer("Напишите так: начать шутить @имя_пользователя")
                    return
                playful_mode.arm(target)
                await message.answer(
                    f"😄 Принято. Когда @{target} напишет в общей группе, "
                    "я обращусь к нему с одной шуткой. Повторяться по таймеру не буду."
                )
                return
            if username == OWNER_USERNAME and lowered.startswith("перестать шутить"):
                target = playful_mode.parse_target(text)
                if not target:
                    await message.answer("Напишите так: перестать шутить @имя_пользователя")
                    return
                removed = playful_mode.cancel(target)
                await message.answer("Команда отменена." if removed else "Для этого участника активной команды нет.")
                return
            return
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return

        stats.register_chat(message.chat.id)
        pending_captcha = join_manager.is_pending(message.chat.id, message.from_user.id)

        if looks_like_ad(message, allow_plain_links=not pending_captcha):
            if not await is_admin(bot, message.chat.id, message.from_user.id):
                await safe_delete_message(bot, message.chat.id, message.message_id)
                stats.increment("ads_removed", chat_id=message.chat.id)
                logger.warning("AD REMOVED chat_id=%s user_id=%s", message.chat.id, message.from_user.id)
                return

        if message.photo and await image_job_ad_filter.image_contains_job_ad(bot, message):
            if not await is_admin(bot, message.chat.id, message.from_user.id):
                await safe_delete_message(bot, message.chat.id, message.message_id)
                stats.increment("ads_removed", chat_id=message.chat.id)
                return

        addressed = is_bot_addressed(message)
        if await moderation.handle_bad_language(bot, message, addressed_to_bot=addressed):
            return

        text = message.text or message.caption or ""
        conversation.remember(message.from_user.id, text)
        if not message.from_user.is_bot:
            display_name = message.from_user.full_name
            if message.from_user.username:
                display_name = f"@{message.from_user.username}"
            conversation.register_activity(message.chat.id, message.from_user.id, display_name)

            opener = playful_mode.take_opener(message.from_user.username or "", display_name)
            if opener:
                await message.reply(opener)
                return

        riddle_reply = riddles.check_answer(message.chat.id, message.from_user.id, text)
        if riddle_reply:
            await message.reply(riddle_reply)
            return

        if not conversation.should_respond(message.from_user.id, text, addressed):
            return

        username = (message.from_user.username or "").lower()
        # Вопросы о возможностях/командах всегда важнее персональных шуточных ответов.
        # Иначе владелец/Юра могли получить «Я слушаю» вместо списка возможностей.
        category = conversation.detect_category(text, addressed=True, continuation=conversation.is_dialog_continuation(message.from_user.id))
        if category == "commands":
            reply = conversation.reply_for(
                message.from_user.id, text, addressed=True, chat_id=message.chat.id
            )
        elif addressed and username == OWNER_USERNAME:
            reply = OWNER_REPLIES[message.from_user.id % len(OWNER_REPLIES)]
        elif addressed and username == YURA_USERNAME:
            reply = YURA_REPLIES[message.from_user.id % len(YURA_REPLIES)]
        else:
            reply = conversation.reply_for(
                message.from_user.id,
                text,
                addressed=addressed,
                chat_id=message.chat.id,
            )
        if reply:
            await message.reply(reply)

    auto_news.register(dp, bot)


async def main() -> None:
    global BOT_ID, BOT_USERNAME
    bot = Bot(TOKEN)
    dp = Dispatcher()

    me = await bot.get_me()
    BOT_ID = me.id
    BOT_USERNAME = me.username or ""
    join_manager.set_bot_identity(BOT_ID, BOT_USERNAME)

    stats.ensure_file()
    await bot.delete_webhook(drop_pending_updates=True)
    await join_manager.start_workers()
    await join_manager.restore_pending(bot)
    await auto_news.start_worker(bot)
    register_handlers(dp, bot)
    report_task = asyncio.create_task(daily_report_worker(bot))
    attack_alert_task = asyncio.create_task(attack_alerts.attack_alert_worker(bot))

    logger.info("Police Bot started as @%s", BOT_USERNAME)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        attack_alert_task.cancel()
        with suppress(asyncio.CancelledError):
            await attack_alert_task
        report_task.cancel()
        with suppress(asyncio.CancelledError):
            await report_task
        await auto_news.stop_worker()
        await join_manager.stop_workers()
        await bot.session.close()


if __name__ == "__main__":
    threading.Thread(target=health_server, daemon=True).start()
    asyncio.run(main())
