import asyncio
import logging
import random
import re
import time
from collections import defaultdict

from aiogram import Bot
from aiogram.types import ChatPermissions, Message


OFFENSE_WINDOW_SECONDS = 10 * 60
TEMP_MUTE_SECONDS = 60
MAX_OFFENSE_KEYS = 5000

offenses: dict[tuple[int, int], list[float]] = defaultdict(list)
logger = logging.getLogger("police.moderation")


async def safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text)
    except Exception as error:
        print("Warning send error:", repr(error))


WARNINGS_FIRST = [
    "\U0001f6e1 \u0421\u043f\u043e\u043a\u043e\u0439\u043d\u0435\u0435. \u0411\u0435\u0437 \u043e\u0441\u043a\u043e\u0440\u0431\u043b\u0435\u043d\u0438\u0439 \u0432 \u0430\u0434\u0440\u0435\u0441 \u0434\u0440\u0443\u0433\u0438\u0445 \u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a\u043e\u0432.",
    "\u26a0\ufe0f \u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435: \u0434\u0435\u0440\u0436\u0438\u043c \u043d\u043e\u0440\u043c\u0430\u043b\u044c\u043d\u044b\u0439 \u0442\u043e\u043d.",
    "\U0001f916 Police \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0435\u0442: \u0441\u043f\u043e\u0440\u0438\u0442\u044c \u043c\u043e\u0436\u043d\u043e, \u043e\u0441\u043a\u043e\u0440\u0431\u043b\u044f\u0442\u044c \u043d\u0435 \u043d\u0430\u0434\u043e.",
]

WARNINGS_SECOND = [
    "\u26a0\ufe0f \u0412\u0442\u043e\u0440\u043e\u0435 \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435. \u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u0435 \u043d\u0430\u0440\u0443\u0448\u0435\u043d\u0438\u0435 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0435 10 \u043c\u0438\u043d\u0443\u0442 \u0434\u0430\u0441\u0442 \u043c\u0443\u0442 \u043d\u0430 1 \u043c\u0438\u043d\u0443\u0442\u0443.",
    "\U0001f6e1 \u0422\u043e\u043d \u0432\u0441\u0435 \u0435\u0449\u0435 \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0440\u0435\u0437\u043a\u0438\u0439. \u0415\u0449\u0435 \u0440\u0430\u0437 - \u0438 \u0431\u0443\u0434\u0435\u0442 \u043c\u0438\u043d\u0443\u0442\u043d\u0430\u044f \u043f\u0430\u0443\u0437\u0430.",
    "\U0001f916 Police \u0444\u0438\u043a\u0441\u0438\u0440\u0443\u0435\u0442 \u043f\u043e\u0432\u0442\u043e\u0440. \u0414\u0430\u043b\u044c\u0448\u0435 \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u0441\u044f \u043a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u043c\u0443\u0442.",
]

BOT_WARNINGS_FIRST = [
    "\U0001f602 \u041d\u0430 \u0431\u043e\u0442\u0430 \u043c\u043e\u0436\u043d\u043e \u0432\u043e\u0440\u0447\u0430\u0442\u044c, \u043d\u043e \u0431\u0435\u0437 \u043b\u0438\u0448\u043d\u0435\u0439 \u0433\u0440\u0443\u0431\u043e\u0441\u0442\u0438. \u042d\u0442\u043e \u043f\u0435\u0440\u0432\u043e\u0435 \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435.",
    "\U0001f6e1 Police \u0436\u0435\u043b\u0435\u0437\u043d\u044b\u0439, \u043d\u043e \u043f\u043e\u0440\u044f\u0434\u043e\u043a \u043b\u044e\u0431\u0438\u0442. \u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435.",
    "\U0001f916 \u042f \u0431\u043e\u0442, \u043d\u0435 \u043e\u0431\u0438\u0436\u0430\u044e\u0441\u044c. \u041d\u043e \u043f\u0440\u0430\u0432\u0438\u043b\u0430 \u0447\u0430\u0442\u0430 \u0432\u0441\u0435 \u0440\u0430\u0432\u043d\u043e \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0442.",
]

MUTE_MESSAGES = [
    "\U0001f6e1 \u0422\u0440\u0435\u0442\u044c\u0435 \u043d\u0430\u0440\u0443\u0448\u0435\u043d\u0438\u0435 \u0437\u0430 10 \u043c\u0438\u043d\u0443\u0442. \u041c\u0443\u0442 \u043d\u0430 1 \u043c\u0438\u043d\u0443\u0442\u0443.",
    "\u26a0\ufe0f Police \u0432\u043a\u043b\u044e\u0447\u0430\u0435\u0442 \u043a\u043e\u0440\u043e\u0442\u043a\u0443\u044e \u043f\u0430\u0443\u0437\u0443: \u043c\u0443\u0442 \u043d\u0430 60 \u0441\u0435\u043a\u0443\u043d\u0434.",
    "\U0001f916 \u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e \u043e\u0441\u043a\u043e\u0440\u0431\u043b\u0435\u043d\u0438\u0439. \u041e\u0442\u0434\u044b\u0445\u0430\u0435\u043c 1 \u043c\u0438\u043d\u0443\u0442\u0443.",
]

WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_@]+")

BAD_WORDS = {
    "\u0434\u0443\u0440\u0430\u043a",
    "\u0434\u0443\u0440\u0430",
    "\u0434\u0443\u0440\u044b",
    "\u0434\u0443\u0440\u043e\u0439",
    "\u0442\u0443\u043f\u043e\u0439",
    "\u0442\u0443\u043f\u0430\u044f",
    "\u0442\u0443\u043f\u044b\u0435",
    "\u0442\u0443\u043f\u0438\u0448\u044c",
    "\u043b\u043e\u0445",
    "\u043b\u043e\u0445\u0438",
    "\u043b\u043e\u0445\u043e\u043c",
    "\u0438\u0434\u0438\u043e\u0442",
    "\u0438\u0434\u0438\u043e\u0442\u043a\u0430",
    "\u0434\u0435\u0431\u0438\u043b",
    "\u0434\u0435\u0431\u0438\u043b\u044b",
    "\u043a\u0440\u0435\u0442\u0438\u043d",
    "\u043f\u0440\u0438\u0434\u0443\u0440\u043e\u043a",
    "\u043f\u0440\u0438\u0434\u0443\u0440\u043a\u0438",
    "\u043c\u0440\u0430\u0437\u044c",
    "\u043c\u0440\u0430\u0437\u0438",
    "\u0441\u0443\u043a\u0430",
    "\u0441\u0443\u043a\u0438",
    "\u0441\u0443\u0447\u043a\u0430",
    "\u0441\u0443\u0447\u0430\u0440\u0430",
}

BAD_PHRASES = {
    ("\u0437\u0430\u0442\u043a\u043d\u0438\u0441\u044c",),
    ("\u0437\u0430\u0442\u043a\u043d\u0438", "\u0440\u043e\u0442"),
    ("\u0437\u0430\u043a\u0440\u043e\u0439", "\u0440\u043e\u0442"),
    ("\u0438\u0434\u0438", "\u043d\u0430\u0445\u0440\u0435\u043d"),
    ("\u043f\u043e\u0448\u0435\u043b", "\u043d\u0430\u0445\u0440\u0435\u043d"),
    ("\u043f\u043e\u0448\u0435\u043b", "\u043d\u0430"),
    ("\u043f\u043e\u0448\u0435\u043b", "\u0442\u044b"),
    ("\u043f\u043e\u0448\u0435\u043b", "\u0432\u043e\u043d"),
    ("\u043e\u0442\u0441\u0442\u0430\u043d\u044c",),
}

TARGET_WORDS = {
    "\u0442\u044b",
    "\u0442\u0435\u0431\u044f",
    "\u0442\u0435\u0431\u0435",
    "\u0442\u043e\u0431\u043e\u0439",
    "\u0432\u044b",
    "\u0432\u0430\u0441",
    "\u0432\u0430\u043c",
    "\u0432\u0430\u043c\u0438",
}

META_WORDS = {
    "\u043c\u0430\u0442",
    "\u043c\u0430\u0442\u0430",
    "\u043c\u0430\u0442\u043e\u043c",
    "\u043e\u0441\u043a\u043e\u0440\u0431\u043b\u0435\u043d\u0438\u0435",
    "\u043e\u0441\u043a\u043e\u0440\u0431\u043b\u0435\u043d\u0438\u044f",
    "\u0441\u043b\u043e\u0432\u043e",
    "\u0441\u043b\u043e\u0432\u0430",
    "\u043f\u0440\u0430\u0432\u0438\u043b\u0430",
    "\u043e\u0431\u0441\u0443\u0436\u0434\u0430\u0435\u043c",
    "\u043e\u0431\u0441\u0443\u0436\u0434\u0435\u043d\u0438\u0435",
    "\u0441\u043a\u0440\u044b\u0442\u044b\u0439",
    "\u0441\u043a\u0440\u044b\u0442\u043e",
}


def words_for_check(text: str) -> list[str]:
    return [match.group(0).lower().replace("\u0451", "\u0435") for match in WORD_RE.finditer(text)]


def contains_phrase(words: list[str], phrase: tuple[str, ...]) -> bool:
    if len(phrase) > len(words):
        return False
    normalized = tuple(part.replace("\u0451", "\u0435") for part in phrase)
    size = len(normalized)
    return any(tuple(words[index:index + size]) == normalized for index in range(len(words) - size + 1))


def contains_bad_words(text: str) -> bool:
    words = words_for_check(text)
    if not words:
        return False
    return any(word in BAD_WORDS for word in words) or any(contains_phrase(words, phrase) for phrase in BAD_PHRASES)




def matched_bad_markers(text: str) -> list[str]:
    """Возвращает точные слова/фразы, из-за которых сообщение признано грубым."""
    words = words_for_check(text)
    matches = [word for word in words if word in BAD_WORDS]
    for phrase in BAD_PHRASES:
        if contains_phrase(words, phrase):
            matches.append(" ".join(phrase))
    return sorted(set(matches))


def log_moderation_decision(
    message: Message,
    *,
    text: str,
    meta: bool,
    bad_markers: list[str],
    targeted: bool,
    addressed_to_bot: bool,
    action: str,
) -> None:
    """Подробная диагностика ложных срабатываний в логах Render."""
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    compact_text = " ".join(text.split())[:300]
    logger.info(
        "MODERATION text=%r chat_id=%s user_id=%s meta=%s bad=%s targeted=%s addressed=%s action=%s",
        compact_text,
        chat_id,
        user_id,
        meta,
        bad_markers,
        targeted,
        addressed_to_bot,
        action,
    )


def is_meta_discussion(text: str) -> bool:
    words = words_for_check(text)
    if not words:
        return False
    if {"\u044d\u0442\u043e", "\u043d\u0435", "\u043c\u0430\u0442"}.issubset(set(words)):
        return True
    return any(word in META_WORDS for word in words)


def has_mention(text: str) -> bool:
    return "@" in text


def has_explicit_target(text: str) -> bool:
    return any(word in TARGET_WORDS for word in words_for_check(text))


def is_targeted(message: Message, addressed_to_bot: bool) -> bool:
    text = message.text or message.caption or ""
    if has_mention(text):
        return True
    if getattr(message, "reply_to_message", None) is not None:
        return True
    if addressed_to_bot:
        return True
    return has_explicit_target(text)


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
        print("Temporary mute error:", repr(error))
        return

    await asyncio.sleep(TEMP_MUTE_SECONDS)

    try:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=unmute_permissions())
    except Exception as error:
        print("Temporary unmute error:", repr(error))


async def handle_bad_language(bot: Bot, message: Message, addressed_to_bot: bool) -> bool:
    if not message.from_user:
        return False

    text = message.text or message.caption or ""
    meta = is_meta_discussion(text)
    bad_markers = matched_bad_markers(text)
    targeted = is_targeted(message, addressed_to_bot)

    if meta:
        log_moderation_decision(
            message, text=text, meta=meta, bad_markers=bad_markers,
            targeted=targeted, addressed_to_bot=addressed_to_bot, action="IGNORE_META",
        )
        return False

    if not bad_markers:
        log_moderation_decision(
            message, text=text, meta=meta, bad_markers=bad_markers,
            targeted=targeted, addressed_to_bot=addressed_to_bot, action="IGNORE_NO_BAD_WORD",
        )
        return False

    if not targeted:
        log_moderation_decision(
            message, text=text, meta=meta, bad_markers=bad_markers,
            targeted=targeted, addressed_to_bot=addressed_to_bot, action="IGNORE_NOT_TARGETED",
        )
        return False

    level = register_offense(message.chat.id, message.from_user.id)
    action = "MUTE_60_SECONDS" if level >= 3 else f"WARN_{level}"
    log_moderation_decision(
        message, text=text, meta=meta, bad_markers=bad_markers,
        targeted=targeted, addressed_to_bot=addressed_to_bot, action=action,
    )
    await safe_reply(message, warning_text(level, addressed_to_bot))

    if level >= 3:
        reset_offenses(message.chat.id, message.from_user.id)
        asyncio.create_task(temporary_mute(bot, message.chat.id, message.from_user.id))

    return True
