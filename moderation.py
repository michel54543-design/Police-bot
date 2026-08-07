import asyncio
import random
import re
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
    "😏 Не быкуй. Я железный, а тебе ещё успокаиваться.",
    "🛡 Фильтруй базар. Шутить можно, грубить — не надо.",
    "🤖 Полегче с тоном. Я не обижусь, а вот правила уже записывают.",
    "Базар поровнее. Мы тут разговариваем, а не рогами меряемся.",
    "Притормози, герой. Тут не ринг.",
    "Сбавь обороты. Мысль можно донести и без наезда.",
    "Эй, полегче. Слова выбирай, это бесплатно.",
    "Тон прибери. Разговор ещё можно спасти.",
    "Не кипятись. Я на месте, а твои нервы — уже нет.",
    "Остынь маленько. Горячая голова плохо думает.",
    "Тише едешь — целее будешь. Пока это только предупреждение.",
    "Не кидайся словами. Они иногда возвращаются.",
    "Спокойнее, команидо. Здесь свои.",
    "Без лишнего шума. Я тебя и так слышу.",
    "Попроще с выражениями. Мы не на разборке.",
    "Пыл умерь. Ещё немного — и тебе самому станет смешно.",
    "Без наездов. Хочешь поговорить — говори нормально.",
    "Не разгоняйся. Тормоза потом дороже обойдутся.",
    "Грубо зашёл. Давай перезайдём нормально.",
    "Язык придержи. Это пока просьба.",
    "Шторм в стакане отменяется. Говори по сути.",
    "Не рычи. Я и с первого раза понимаю.",
    "Ты сейчас с ботом споришь. Подумай, как мы до этого дошли.",
    "Напор засчитан, аргументы — нет. Попробуй ещё раз.",
    "Резковато. Давай без цирка и по делу.",
    "Не козыряй грубостью. Карта слабая.",
    "Давай без этого уличного театра. Скажи прямо, что не нравится.",
    "Не заводись. Я не соперник, я табличка «успокойся».",
    "Понижаем градус. До мута ещё можно не доводить.",
    "Словесную дубину убери. Нормально обсудим.",
    "Громко — не значит убедительно. Давай по новой.",
    "Ты мне не враг. Не надо так напрягаться.",
]

MUTE_MESSAGES = [
    "🛡 Третье нарушение за 10 минут. Мут на 1 минуту.",
    "⚠️ Police включает короткую паузу: мут на 60 секунд.",
    "🤖 Слишком много оскорблений. Отдыхаем 1 минуту.",
]


def contains_bad_words(text: str) -> bool:
    lowered = text.lower()
    # Короткое «нах» ищем как отдельное ругательство, а не как часть
    # обычных слов: «находится», «находка» и т. п.
    if re.search(r"(?<![\wа-яё])нах(?:уй|ер)?(?![\wа-яё])", lowered):
        return True
    return any(marker in lowered for marker in BAD_WORD_MARKERS if marker != "нах")


def starts_with_bot_address(text: str) -> bool:
    """«Бот пошёл…» — явное обращение даже без запятой."""
    return re.match(r"^\s*бот(?:\s+|[,.:;!?—-])", text.lower()) is not None


def has_mention(text: str) -> bool:
    return "@" in text


def is_targeted(message: Message, addressed_to_bot: bool) -> bool:
    text = message.text or message.caption or ""
    if addressed_to_bot or starts_with_bot_address(text):
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

    targeted_at_bot = addressed_to_bot or starts_with_bot_address(text)
    if not is_targeted(message, targeted_at_bot):
        return True

    level = register_offense(message.chat.id, message.from_user.id)
    await safe_reply(message, warning_text(level, targeted_at_bot))

    if level >= 3:
        reset_offenses(message.chat.id, message.from_user.id)
        asyncio.create_task(temporary_mute(bot, message.chat.id, message.from_user.id))

    return True
