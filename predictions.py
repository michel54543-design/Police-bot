import json
import random
import time
from collections import deque
from pathlib import Path


PREDICTIONS_PATH = Path(__file__).resolve().parent / "predictions.json"
COOLDOWN_SECONDS = 60
RECENT_LIMIT = 300

last_use: dict[int, float] = {}
recent: deque[int] = deque(maxlen=RECENT_LIMIT)

COOLDOWN_REPLIES = [
    "🔮 Шар предсказаний остывает. Подожди {seconds} сек.",
    "✨ Судьба просит паузу на {seconds} сек.",
    "😄 Не дёргай будущее так часто. Осталось {seconds} сек.",
    "🛡 Police сверяет знаки. Ещё {seconds} сек.",
    "⚔️ Следующее предсказание будет через {seconds} сек.",
]


def load() -> list[str]:
    with PREDICTIONS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    items = data.get("predictions", data) if isinstance(data, dict) else data
    return [str(item).strip() for item in items if str(item).strip()]


PREDICTIONS = load()


def seconds_left(user_id: int) -> int:
    last = last_use.get(user_id)
    if last is None:
        return 0
    return max(0, COOLDOWN_SECONDS - int(time.monotonic() - last))


def can_use(user_id: int) -> bool:
    return seconds_left(user_id) == 0


def cooldown_text(user_id: int) -> str:
    return random.choice(COOLDOWN_REPLIES).format(seconds=seconds_left(user_id))


def get_prediction(user_id: int) -> str:
    last_use[user_id] = time.monotonic()
    if not PREDICTIONS:
        return "Сегодня будущее молчит, но удача всё равно где-то рядом."
    available = [index for index in range(len(PREDICTIONS)) if index not in recent]
    if not available:
        recent.clear()
        available = list(range(len(PREDICTIONS)))
    index = random.choice(available)
    recent.append(index)
    return PREDICTIONS[index]
