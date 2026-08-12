import json
import random
import time
from collections import deque
from pathlib import Path


TOASTS_PATH = Path(__file__).resolve().parent / "toasts.json"
COOLDOWN_SECONDS = 60
RECENT_LIMIT = 300

last_use: dict[int, float] = {}
recent: deque[int] = deque(maxlen=RECENT_LIMIT)

COOLDOWN_REPLIES = [
    "🍻 Тост должен настояться. Подожди {seconds} сек.",
    "🥂 Кубок на перезарядке. Осталось {seconds} сек.",
    "😄 Не торопи застолье. Ещё {seconds} сек.",
    "🛡 Police держит паузу перед новым тостом: {seconds} сек.",
    "🍺 Следующий тост нальётся через {seconds} сек.",
]


def load() -> list[str]:
    with TOASTS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    items = data.get("toasts", data) if isinstance(data, dict) else data
    return [str(item).strip() for item in items if str(item).strip()]


TOASTS = load()


def seconds_left(user_id: int) -> int:
    last = last_use.get(user_id)
    if last is None:
        return 0
    return max(0, COOLDOWN_SECONDS - int(time.monotonic() - last))


def can_use(user_id: int) -> bool:
    return seconds_left(user_id) == 0


def cooldown_text(user_id: int) -> str:
    return random.choice(COOLDOWN_REPLIES).format(seconds=seconds_left(user_id))


def get_toast(user_id: int) -> str:
    last_use[user_id] = time.monotonic()
    if not TOASTS:
        return "Тосты временно закончились. Поднимем кружку за терпение."
    available = [index for index in range(len(TOASTS)) if index not in recent]
    if not available:
        recent.clear()
        available = list(range(len(TOASTS)))
    index = random.choice(available)
    recent.append(index)
    return TOASTS[index]
