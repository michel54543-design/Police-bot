"""Контекстный разговорный движок и учёт активных участников."""
import random
import re
import time
from collections import defaultdict, deque

from dialog_database import COMMANDS_TEXT, INTENT_PHRASES, RESPONSES
from fun_responses import HUMOR_RESPONSES, LONG_DIALOG_JOKES, rare_easter_egg

MEMORY_SECONDS = 15 * 60
MAX_MESSAGES = 8
ACTIVE_WINDOW_SECONDS = 6 * 60 * 60
SUGGESTION_COOLDOWN_SECONDS = 3 * 60 * 60

memory: dict[int, deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=MAX_MESSAGES))
last_explicit_address_at: dict[int, float] = {}
last_category: dict[int, str] = {}
consecutive_addresses: dict[int, int] = defaultdict(int)
last_reply: dict[tuple[int, str], str] = {}

activity: dict[int, dict[int, deque[float]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=80)))
display_names: dict[tuple[int, int], str] = {}
last_suggestion_at: dict[tuple[int, int], float] = {}
last_suggested_user: dict[tuple[int, int], int] = {}


def cleanup(user_id: int) -> None:
    now = time.monotonic()
    user_memory = memory[user_id]
    while user_memory and now - user_memory[0][0] > MEMORY_SECONDS:
        user_memory.popleft()
    last_address = last_explicit_address_at.get(user_id)
    if last_address is not None and now - last_address > MEMORY_SECONDS:
        last_explicit_address_at.pop(user_id, None)
        last_category.pop(user_id, None)
        consecutive_addresses[user_id] = 0


def remember(user_id: int, text: str) -> None:
    cleanup(user_id)
    memory[user_id].append((time.monotonic(), text.strip().lower()))


def register_activity(chat_id: int, user_id: int, name: str) -> None:
    now = time.monotonic()
    messages = activity[chat_id][user_id]
    messages.append(now)
    while messages and now - messages[0] > ACTIVE_WINDOW_SECONDS:
        messages.popleft()
    display_names[(chat_id, user_id)] = name.strip() or "участник чата"


def is_dialog_continuation(user_id: int) -> bool:
    cleanup(user_id)
    last_address = last_explicit_address_at.get(user_id)
    return last_address is not None and time.monotonic() - last_address <= MEMORY_SECONDS


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def detect_category(text: str, addressed: bool = False, continuation: bool = False) -> str | None:
    normalized = _normalized(text)
    for category, phrases in INTENT_PHRASES.items():
        if any(_normalized(phrase) in normalized for phrase in phrases):
            return category
    if continuation or addressed:
        return "fallback"
    return None


def should_respond(user_id: int, text: str, addressed: bool = False) -> bool:
    # В группе распознанная фраза сама по себе не является приглашением боту:
    # участники могут приветствовать и благодарить друг друга.
    return addressed and detect_category(text, addressed=True, continuation=is_dialog_continuation(user_id)) is not None


def _choose(user_id: int, category: str, options: list[str] | tuple[str, ...]) -> str | None:
    if not options:
        return None
    previous = last_reply.get((user_id, category))
    available = [item for item in options if item != previous] or list(options)
    result = random.choice(available)
    last_reply[(user_id, category)] = result
    return result


def _mark_dialog(user_id: int, category: str) -> int:
    cleanup(user_id)
    if not is_dialog_continuation(user_id):
        consecutive_addresses[user_id] = 0
    consecutive_addresses[user_id] += 1
    last_explicit_address_at[user_id] = time.monotonic()
    last_category[user_id] = category
    return consecutive_addresses[user_id]


def _active_suggestion(chat_id: int, user_id: int, count: int) -> str | None:
    if not chat_id or count < 9 or random.random() >= 0.28:
        return None
    key = (chat_id, user_id)
    now = time.monotonic()
    if now - last_suggestion_at.get(key, 0) < SUGGESTION_COOLDOWN_SECONDS:
        return None
    candidates = []
    for candidate_id, timestamps in activity.get(chat_id, {}).items():
        while timestamps and now - timestamps[0] > ACTIVE_WINDOW_SECONDS:
            timestamps.popleft()
        if candidate_id != user_id and len(timestamps) >= 3 and candidate_id != last_suggested_user.get(key):
            candidates.append(candidate_id)
    if not candidates:
        return None
    candidate_id = random.choice(candidates)
    name = display_names.get((chat_id, candidate_id), "один из участников")
    last_suggestion_at[key] = now
    last_suggested_user[key] = candidate_id
    return random.choice((
        f"Кстати, сегодня активно общается {name}.",
        f"Попробуй написать {name} — сегодня он часто бывает в чате.",
        f"Кроме меня сегодня очень разговорчив {name}.",
    ))


def reply_for(user_id: int, text: str, addressed: bool = False, chat_id: int = 0) -> str | None:
    if not addressed:
        return None
    continuation = is_dialog_continuation(user_id)
    category = detect_category(text, addressed, continuation)
    if category is None:
        return None
    count = _mark_dialog(user_id, category)
    easter_egg = rare_easter_egg()
    if easter_egg:
        return easter_egg
    if category == "commands":
        return COMMANDS_TEXT
    suggestion = _active_suggestion(chat_id, user_id, count)
    if suggestion:
        return suggestion
    if count >= 7 and random.random() < 0.45:
        return _choose(user_id, "long_joke", LONG_DIALOG_JOKES + HUMOR_RESPONSES)
    if count >= 5 and random.random() < 0.22:
        return _choose(user_id, "humor", HUMOR_RESPONSES)
    if count >= 4 and random.random() < 0.55:
        return _choose(user_id, "frequent", RESPONSES["frequent"])
    return _choose(user_id, category, RESPONSES.get(category, RESPONSES["fallback"]))
