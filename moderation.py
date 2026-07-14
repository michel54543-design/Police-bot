import asyncio
import random
import time
from collections import defaultdict
from typing import Any

from aiogram import Bot
from aiogram.types import ChatPermissions, Message


OFFENSE_WINDOW_SECONDS = 10 * 60
TEMP_MUTE_SECONDS = 60
MAX_OFFENSE_KEYS = 5000

offenses: dict[tuple[int, int], list[float]] = defaultdict(list)

async def safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text)
    except Exception as error:
        print("Ошибка отправки предупреждения:", repr(error))


BAD_WORD_MARKERS = [
    "дурак",
    "тупой",
    "тупая",
    "тупишь",
    "лох",
    "заткнись",
    "молчи",
    "идиот",
    "дебил",
    "кретин",
    "придурок",
    "отстань",
    "бесишь",
    "пошел",
    "пошёл",
    "нах",
    "хрен",
    "блин",
    "сука",
    "суч",
    "мраз",
]

UNTARGETED_REPLIES = [
    "🍺 Сурово сказано. Но чат лучше держать спокойным.",
    "🛡 Police услышал эмоции и сделал вид, что протокол занят.",
    "😂 Бывает. Главное, без наездов на людей.",
    "🤖 Мат принят как шум таверны. Продолжаем без пожара.",
    "⚔️ Энергию лучше оставить для рекламщиков.",
]

WARNINGS_FIRST = [
    "🛡 Спокойнее. Без оскорблений в адрес других участников.",
    "⚠️ Первое предупреждение: держим нормальный тон.",
    "🤖 Police напоминает: спорить можно, оскорблять не надо.",
]

WARNINGS_SECOND = [
    "⚠️ Второе предупреждение. Следующее нарушение в течение 10 минут даст мут на 1 минуту.",
    "🛡 Тон всё ещё слишком резкий. Ещё раз — и будет минутная пауза.",
    "🤖 Police фиксирует повтор. Дальше включится короткий мут.",
]

BOT_WARNINGS_FIRST = [
    "😂 На бота можно ворчать, но без лишней грубости. Это первое предупреждение.",
    "🛡 Police железный, но порядок любит. Первое предупреждение.",
    "🤖 Я бот, не обижаюсь. Но правила чата всё равно работают.",
]

MUTE_MESSAGES = [
    "🛡 Третье нарушение за 10 минут. Мут на 1 минуту.",
    "⚠️ Police включает короткую паузу: мут на 60 секунд.",
    "🤖 Слишком много оскорблений. Отдыхаем 1 минуту.",
]


def contains_bad_words(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BAD_WORD_MARKERS)


def has_mention(text: str) -> bool:
    return "@" in text


def is_targeted(message: Message, addressed_to_bot: bool) -> bool:
    text = message.text or message.caption or ""
    if addressed_to_bot:
        return True
    if getattr(message, "reply_to_message", None) is not None:
        return True
    return has_mention(text)


def register_offense(chat_id: int, user_id: int) -> int:
    now = time.monotonic()
    cleanup_offenses(now)
    key = (chat_id, user_id)
    recent = [stamp for stamp in offenses[key] if now - stamp <= OFFENSE_WINDOW_SECONDS]
    recent.append(now)
    offenses[key] = recent
    return len(recent)


def cleanup_offenses(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        key for key, stamps in offenses.items()
        if not stamps or now - max(stamps) > OFFENSE_WINDOW_SECONDS
    ]
    for key in expired:
        offenses.pop(key, None)

    if len(offenses) <= MAX_OFFENSE_KEYS:
        return

    overflow = sorted(offenses, key=lambda key: max(offenses[key]))[: len(offenses) - MAX_OFFENSE_KEYS]
    for key in overflow:
        offenses.pop(key, None)


def reset_offenses(chat_id: int, user_id: int) -> None:
    offenses.pop((chat_id, user_id), None)


def warning_text(level: int, addressed_to_bot: bool) -> str:
    if level <= 1:
        source = BOT_WARNINGS_FIRST if addressed_to_bot else WARNINGS_FIRST
    elif level == 2:
        source = WARNINGS_SECOND
    else:
        source = MUTE_MESSAGES
    return random.choice(source)


def unmute_permissions() -> ChatPermissions:
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


def mute_permissions() -> ChatPermissions:
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


async def temporary_mute(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=mute_permissions())
    except Exception as error:
        print("Ошибка временного мута:", repr(error))
        return

    await asyncio.sleep(TEMP_MUTE_SECONDS)

    try:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=unmute_permissions())
    except Exception as error:
        print("Ошибка снятия временного мута:", repr(error))


async def handle_bad_language(bot: Bot, message: Message, addressed_to_bot: bool) -> bool:
    if not message.from_user:
        return False

    text = message.text or message.caption or ""
    if not contains_bad_words(text):
        return False

    if not is_targeted(message, addressed_to_bot):
        if random.random() < 0.35:
            await safe_reply(message, random.choice(UNTARGETED_REPLIES))
        return True

    level = register_offense(message.chat.id, message.from_user.id)
    await safe_reply(message, warning_text(level, addressed_to_bot))

    if level >= 3:
        reset_offenses(message.chat.id, message.from_user.id)
        asyncio.create_task(temporary_mute(bot, message.chat.id, message.from_user.id))

    return True
