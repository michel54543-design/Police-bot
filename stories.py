import json
import random
import time
from collections import deque
from pathlib import Path


STORIES_PATH = Path(__file__).resolve().parent / "stories.json"
COOLDOWN_SECONDS = 60
RECENT_LIMIT = 300

last_use: dict[int, float] = {}
recent: deque[int] = deque(maxlen=RECENT_LIMIT)

COOLDOWN_REPLIES = [
    "📖 Истории любят паузу. Подожди ещё {seconds} сек.",
    "🍺 Рассказчик набирает воздуха. Осталось {seconds} сек.",
    "😄 Ты читаешь быстрее, чем Police вспоминает истории. Ещё {seconds} сек.",
    "🛡 Архив историй на короткой перезарядке: {seconds} сек.",
    "🔥 Следующая история будет через {seconds} сек.",
]


def load() -> list[str]:
    with STORIES_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    items = data.get("stories", data) if isinstance(data, dict) else data
    return [str(item).strip() for item in items if str(item).strip()]


STORIES = load()


def seconds_left(user_id: int) -> int:
    last = last_use.get(user_id)
    if last is None:
        return 0
    return max(0, COOLDOWN_SECONDS - int(time.monotonic() - last))


def can_use(user_id: int) -> bool:
    return seconds_left(user_id) == 0


def cooldown_text(user_id: int) -> str:
    return random.choice(COOLDOWN_REPLIES).format(seconds=seconds_left(user_id))


def get_story(user_id: int) -> str:
    last_use[user_id] = time.monotonic()
    if not STORIES:
        return "Истории временно закончились. Police уже ищет нового рассказчика."
    available = [index for index in range(len(STORIES)) if index not in recent]
    if not available:
        recent.clear()
        available = list(range(len(STORIES)))
    index = random.choice(available)
    recent.append(index)
    return STORIES[index]
