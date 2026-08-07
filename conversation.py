import json
import random
import time
from collections import defaultdict, deque
from pathlib import Path

from personality import get_mood
from reply_selector import choose


MEMORY_SECONDS = 15 * 60
MAX_MESSAGES = 8
RUDE_REPLY_COOLDOWN = 30

REPLIES_PATH = Path(__file__).resolve().parent / "police_replies.json"

memory: dict[int, deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=MAX_MESSAGES))
last_explicit_address_at: dict[int, float] = {}
last_category: dict[int, str] = {}
consecutive_addresses: dict[int, int] = defaultdict(int)
last_rude_reply_at: dict[int, float] = {}
last_rude_reply_text: dict[int, str] = {}

RUDE_MARKERS = [
    "дурак",
    "тупой",
    "тупишь",
    "заткнись",
    "молчи",
    "отстань",
    "надоел",
    "бесишь",
    "кривой бот",
    "бот тупит",
    "плохой бот",
    "глупый бот",
    "иди отсюда",
    "иди лесом",
    "не мешай",
    "что ты несешь",
    "чего ты несешь",
    "ты сломан",
    "бот сломан",
]

RUDE_REPLIES = [
    "😎 Полегче, воин. Police не обижается, но протокол всё записал.",
    "🛡 Грубость замечена. Ответ стража: спокойно, без паники.",
    "🤖 Я бот, у меня нервы железные. Но давай без наездов.",
    "😂 Ого, режим сурового викинга включён. Дышим ровно.",
    "⚔️ Не путай Police с противником на арене. Я за порядок.",
    "🍺 Спокойнее, герой. Лучше направь эту энергию на рекламщиков.",
    "🪓 Я бы поднял виртуальный топор, но сегодня работаем культурно.",
    "🤖 На бота наехал? Смело. Бесполезно, но смело.",
    "🛡 Страж услышал тон. Страж предлагает снизить громкость.",
    "😂 Я не обижаюсь. У меня такой функции просто нет.",
    "⚔️ В чате можно быть грозным, но лучше быть вежливым.",
    "🍻 Давай без резких выпадов. Напиши /анекдот и перезагрузим настроение.",
    "🤖 Я всего лишь код, но даже код любит нормальное общение.",
    "😎 Police на посту. Провокации приняты, но не обработаны.",
    "🛡 Спокойно, викинг. Я охраняю чат, а не спорю в таверне.",
    "😂 Сейчас даже ленивый модератор проснулся бы от такого тона.",
    "⚔️ Не трать ярость на бота. Рекламщики ждут где-то за углом.",
    "🍺 Я на твоей стороне, пока ты не спамишь и не грубишь.",
    "🤖 Сообщение принято. Ответ: чуть мягче, пожалуйста.",
    "🪓 Виртуальный топор оставим для рекламы. Тут хватит спокойного разговора.",
]


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
    # Короткий оклик — это call. Длинную фразу с именем бота не сводим
    # к бесконечному «Я тут»: это уже реплика диалога.
    words = normalized.replace(",", " ").replace("!", " ").replace("?", " ").split()
    if addressed and len(words) <= 2 and contains_any(normalized, ["police", "бот"]):
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

    if count >= 6:
        frequent = choose(f"user:{user_id}:frequent", REPLIES.get("frequent", []), mood)
        if frequent and random.random() < 0.25:
            return frequent

    return choose(f"user:{user_id}:{category}", REPLIES.get(category, REPLIES.get("fallback", [])), mood)


def looks_rude(text: str) -> bool:
    normalized = text.strip().lower()
    return any(marker in normalized for marker in RUDE_MARKERS)


def can_reply_rude(user_id: int) -> bool:
    now = time.monotonic()
    last = last_rude_reply_at.get(user_id, 0)
    if now - last < RUDE_REPLY_COOLDOWN:
        return False
    last_rude_reply_at[user_id] = now
    return True


def rude_reply(user_id: int) -> str:
    last_text = last_rude_reply_text.get(user_id)
    available = [text for text in RUDE_REPLIES if text != last_text]
    if not available:
        available = RUDE_REPLIES
    text = random.choice(available)
    last_rude_reply_text[user_id] = text
    return text
