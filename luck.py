import json
import random
import time
from collections import deque
from pathlib import Path


COMMENTS_PATH = Path(__file__).resolve().parent / "luck_comments.json"
COOLDOWN_SECONDS = 60
RECENT_LIMIT = 100

last_use: dict[int, float] = {}
recent: deque[int] = deque(maxlen=RECENT_LIMIT)

COOLDOWN_REPLIES = [
    "🍀 Фарт перезаряжается. Подожди {seconds} сек.",
    "🎲 Кубики ещё катятся. Осталось {seconds} сек.",
    "😄 Удача не любит, когда её дёргают каждую секунду. Ещё {seconds} сек.",
    "⚔️ Следующий замер фарта через {seconds} сек.",
    "🍺 Фарт ушёл за пивом. Вернётся через {seconds} сек.",
]


def load() -> list[str]:
    with COMMENTS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    items = data.get("comments", data) if isinstance(data, dict) else data
    return [str(item).strip() for item in items if str(item).strip()]


COMMENTS = load()


def seconds_left(user_id: int) -> int:
    last = last_use.get(user_id)
    if last is None:
        return 0
    return max(0, COOLDOWN_SECONDS - int(time.monotonic() - last))


def can_use(user_id: int) -> bool:
    return seconds_left(user_id) == 0


def cooldown_text(user_id: int) -> str:
    return random.choice(COOLDOWN_REPLIES).format(seconds=seconds_left(user_id))


def get_luck(user_id: int) -> str:
    last_use[user_id] = time.monotonic()
    percent = random.randint(1, 100)
    if not COMMENTS:
        return f"🍀 Фарт сегодня: {percent}%."
    available = [index for index in range(len(COMMENTS)) if index not in recent]
    if not available:
        recent.clear()
        available = list(range(len(COMMENTS)))
    index = random.choice(available)
    recent.append(index)
    return f"🍀 Фарт сегодня: {percent}%\n\n{COMMENTS[index]}"
