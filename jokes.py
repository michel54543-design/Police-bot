import json
import random
import time
from collections import deque
from pathlib import Path

from config import ALLOW_DIRTY_JOKES


JOKES_PATH = Path(__file__).resolve().parent / "jokes.json"
COOLDOWN_SECONDS = 60
RECENT_LIMIT = 500

last_use: dict[int, float] = {}
recent: deque[int] = deque(maxlen=RECENT_LIMIT)

COOLDOWN_REPLIES = [
    "😂 Ты так смеёшься, что база не успевает заряжаться. Осталось {seconds} сек.",
    "🍺 Передозировка юмором! Подожди ещё {seconds} сек.",
    "🤣 Хватит ржать, дай другим посмеяться! Осталось {seconds} сек.",
    "😎 Конвейер анекдотов перегрелся. Подожди {seconds} сек.",
    "🛡 Police уже выдал шутку. Перезарядка: {seconds} сек.",
    "🤖 Юмор в обработке. Следующая порция через {seconds} сек.",
    "🍻 Таверна просит паузу. Осталось {seconds} сек.",
    "😂 Смех продлевает жизнь, но команда отдыхает ещё {seconds} сек.",
    "⚔️ Анекдотный меч перезаряжается. {seconds} сек.",
    "🪓 Не руби команду так часто. Подожди {seconds} сек.",
    "😴 Police ещё смеётся с прошлого анекдота. {seconds} сек.",
    "🛡 Очередь шуток закрыта на {seconds} сек.",
    "🤖 Система юмора охлаждается. {seconds} сек.",
    "🍺 Следующий смешок через {seconds} сек.",
    "😂 Ты нажал на кнопку смеха слишком рано. {seconds} сек.",
    "⚔️ Терпение, воин. Анекдот будет через {seconds} сек.",
    "😎 Юмор любит паузу. Осталось {seconds} сек.",
    "🛡 Police не флудит анекдотами. Ждём {seconds} сек.",
    "🍻 Не торопи бармена. Анекдот нальют через {seconds} сек.",
    "🪓 Топор юмора на паузе. {seconds} сек.",
]


DIRTY_MARKERS = (
    "бля", "пизд", "хуй", "хуе", "нахуй", "заеб", "ебал", "ебан",
    "охуел", "охрен", "сука", "жоп", "хер", "нахрен",
)


def is_dirty(text: str) -> bool:
    lowered = text.lower().replace("ё", "е")
    return any(marker in lowered for marker in DIRTY_MARKERS)


def load() -> list[str]:
    with JOKES_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, dict):
        items = data.get("jokes", [])
    else:
        items = data

    jokes = [str(item).strip() for item in items if str(item).strip()]
    if not ALLOW_DIRTY_JOKES:
        jokes = [item for item in jokes if not is_dirty(item)]
    return jokes


JOKES = load()


def seconds_left(user_id: int) -> int:
    return max(0, COOLDOWN_SECONDS - int(time.monotonic() - last_use.get(user_id, 0)))


def can_use(user_id: int) -> bool:
    return seconds_left(user_id) == 0


def cooldown_text(user_id: int) -> str:
    return random.choice(COOLDOWN_REPLIES).format(seconds=seconds_left(user_id))


def get_joke(user_id: int) -> str:
    last_use[user_id] = time.monotonic()
    if not JOKES:
        return "Анекдоты временно закончились. Police уже ищет, где они спрятались."
    available = [index for index in range(len(JOKES)) if index not in recent]
    if not available:
        recent.clear()
        available = list(range(len(JOKES)))
    index = random.choice(available)
    recent.append(index)
    return JOKES[index]
