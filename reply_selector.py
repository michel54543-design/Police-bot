import random
import time
from collections import defaultdict, deque
from typing import Any


RECENT_LIMIT = 50
HISTORY_TTL_SECONDS = 24 * 60 * 60
MAX_HISTORY_KEYS = 5000
recent_indices: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=RECENT_LIMIT))
last_used_at: dict[str, float] = {}


def cleanup_histories() -> None:
    if len(recent_indices) <= MAX_HISTORY_KEYS:
        return

    now = time.monotonic()
    expired = [key for key, stamp in last_used_at.items() if now - stamp > HISTORY_TTL_SECONDS]
    for key in expired:
        recent_indices.pop(key, None)
        last_used_at.pop(key, None)

    if len(recent_indices) <= MAX_HISTORY_KEYS:
        return

    overflow = sorted(last_used_at, key=last_used_at.get)[: len(recent_indices) - MAX_HISTORY_KEYS]
    for key in overflow:
        recent_indices.pop(key, None)
        last_used_at.pop(key, None)


def item_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text", "")).strip()
    return str(item).strip()


def item_matches_mood(item: Any, mood: str) -> bool:
    if not isinstance(item, dict):
        return True
    if "mood" in item:
        return item["mood"] == mood
    if "moods" in item:
        return mood in item["moods"]
    return True


def choose(key: str, items: list[Any], mood: str | None = None) -> str:
    if not items:
        return ""

    cleanup_histories()
    last_used_at[key] = time.monotonic()

    candidate_indexes = list(range(len(items)))
    if mood:
        mood_indexes = [index for index, item in enumerate(items) if item_matches_mood(item, mood)]
        if len(mood_indexes) >= 3:
            candidate_indexes = mood_indexes

    recent = recent_indices[key]
    if len(candidate_indexes) > 1:
        fresh = [index for index in candidate_indexes if index not in recent]
        if not fresh and len(items) <= RECENT_LIMIT:
            fresh = [index for index in candidate_indexes if index != (recent[-1] if recent else -1)]
        if fresh:
            candidate_indexes = fresh

    index = random.choice(candidate_indexes)
    recent.append(index)
    return item_text(items[index])
