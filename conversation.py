import json
import random
import time
from collections import defaultdict, deque
from pathlib import Path

from personality import get_mood
from reply_selector import choose


MEMORY_SECONDS = 15 * 60
MAX_MESSAGES = 8

REPLIES_PATH = Path(__file__).resolve().parent / "police_replies.json"

memory: dict[int, deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=MAX_MESSAGES))
last_explicit_address_at: dict[int, float] = {}
last_category: dict[int, str] = {}
consecutive_addresses: dict[int, int] = defaultdict(int)

def load_replies() -> dict[str, list[str]]:
    with REPLIES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


REPLIES = load_replies()


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


def is_dialog_continuation(user_id: int) -> bool:
    cleanup(user_id)
    last_address = last_explicit_address_at.get(user_id)
    return last_address is not None and time.monotonic() - last_address <= MEMORY_SECONDS


def mark_addressed(user_id: int, category: str) -> int:
    cleanup(user_id)
    now = time.monotonic()
    last_address = last_explicit_address_at.get(user_id)
    if last_address is None or now - last_address > MEMORY_SECONDS:
        consecutive_addresses[user_id] = 0
    consecutive_addresses[user_id] += 1
    last_explicit_address_at[user_id] = now
    last_category[user_id] = category
    return consecutive_addresses[user_id]


def contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def detect_category(text: str, addressed: bool, continuation: bool) -> str | None:
    normalized = text.strip().lower()

    if contains_any(normalized, ["спасибо", "благодарю", "молодец", "красавчик"]):
        return "thanks"
    if contains_any(normalized, ["привет", "здравствуй", "доброе утро", "добрый вечер", "здарова"]):
        return "hello"
    if contains_any(normalized, ["как дела", "как настроение", "всё нормально", "все нормально", "как ты"]):
        return "how_are_you"
    if contains_any(normalized, ["что делаешь", "чем занят", "ты где", "где ты"]):
        return "what_doing"
    if contains_any(normalized, ["кто ты", "что ты умеешь", "кто тебя создал", "кто твой хозяин"]):
        return "who_are_you"
    if contains_any(normalized, ["спишь", "не спишь", "ты уснул", "проснись"]):
        return "sleep"
    if contains_any(normalized, ["скучно", "расскажи что-нибудь", "развесели"]):
        return "bored"
    if contains_any(normalized, ["пока", "до встречи", "спокойной ночи"]):
        return "goodbye"
    if addressed and contains_any(normalized, ["police", "бот"]):
        return "call"
    if continuation:
        return "fallback"
    return "fallback" if addressed else None


def reply_for(user_id: int, text: str, addressed: bool = False) -> str | None:
    if not addressed:
        cleanup(user_id)
        return None

    continuation = is_dialog_continuation(user_id)
    category = detect_category(text, addressed, continuation)
    if category is None:
        return None

    count = mark_addressed(user_id, category)
    mood = get_mood()

    if count >= 4:
        frequent = choose(f"user:{user_id}:frequent", REPLIES.get("frequent", []), mood)
        if frequent and (count >= 7 or random.random() < 0.65):
            return frequent

    return choose(f"user:{user_id}:{category}", REPLIES.get(category, REPLIES.get("fallback", [])), mood)
