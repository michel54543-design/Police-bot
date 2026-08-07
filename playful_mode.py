"""Одноразовое начало шуточного разговора по личной команде владельца."""
import json
import random
import re
from pathlib import Path


STATE_PATH = Path(__file__).resolve().parent / "playful_targets.json"
TARGET_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,32})|@([A-Za-z0-9_]{5,32})", re.I)

OPENERS = (
    "{name}, говорят, ты сегодня отвечаешь за хорошее настроение. Я пришёл проверить показания свидетелей 😄",
    "{name}, короткий вопрос: ты всегда такой серьёзный или это парадная форма? 😄",
    "{name}, Police получил ориентировку: в чате слишком спокойно. Есть идеи, как исправить?",
    "{name}, я хотел пошутить тонко, но служебная инструкция требует сначала поздороваться 😄",
    "{name}, у меня к тебе дело особой важности: кто сегодня отвечает за смех в чате?",
    "{name}, признавайся: хорошее настроение при себе имеется или будем оформлять розыск?",
    "{name}, не пугайся, это не допрос. Просто у бота закончился собеседник 😄",
    "{name}, проверка связи: если меня слышно — скажи что-нибудь, что не стыдно занести в протокол.",
    "{name}, я тут подумал: молчать профессионально умею, но шутить получается веселее.",
    "{name}, служебный вопрос: почему чат без тебя подозрительно тихий?",
    "{name}, сегодня у меня режим доброго полицейского. Злой ушёл пить кофе 😄",
    "{name}, предлагаю сделку: ты начинаешь разговор, а я постараюсь не испортить его служебным юмором.",
)


def _load() -> dict[str, bool]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {str(key).lower(): bool(value) for key, value in raw.items()}
    except (OSError, ValueError, TypeError):
        return {}


targets = _load()


def _save() -> None:
    STATE_PATH.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_target(text: str) -> str | None:
    match = TARGET_RE.search(text or "")
    if not match:
        return None
    return (match.group(1) or match.group(2)).lower()


def arm(username: str) -> None:
    targets[username.lower().lstrip("@")] = True
    _save()


def cancel(username: str) -> bool:
    removed = targets.pop(username.lower().lstrip("@"), None) is not None
    if removed:
        _save()
    return removed


def take_opener(username: str, display_name: str) -> str | None:
    key = (username or "").lower().lstrip("@")
    if not key or not targets.pop(key, None):
        return None
    _save()
    return random.choice(OPENERS).format(name=display_name)
