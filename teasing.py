import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


TARGETS_PATH = Path(__file__).resolve().parent / "tease_targets.json"
ACTIVE_MEMBER_SECONDS = 30 * 60


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def load_targets() -> set[str]:
    if not TARGETS_PATH.exists():
        return set()
    try:
        data = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
        return {normalize_username(item) for item in data if normalize_username(str(item))}
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


targets = load_targets()
playful_users: set[str] = set()
active_members: dict[int, dict[str, tuple[float, str]]] = defaultdict(dict)


def save_targets() -> None:
    temporary = TARGETS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(sorted(targets), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(TARGETS_PATH)


def parse_owner_command(text: str) -> tuple[str, str | None] | None:
    normalized = " ".join(text.strip().lower().split())
    if normalized in {"кого подкалываю", "кого ты подкалываешь", "список подколов"}:
        return "list", None

    # Принимаем @username, username и полную ссылку t.me/username.
    # Слово «с» необязательно, чтобы команду было удобно вводить с телефона.
    target = r"(?:https?://)?(?:t\.me/)?@?([a-zA-Z0-9_]{3,32})/?"
    patterns = (
        ("start", rf"^(?:начать|начни) шутить(?: с)?\s+{target}$"),
        ("stop", rf"^(?:перестать|перестань) шутить(?: с)?\s+{target}$"),
    )
    for action, pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return action, normalize_username(match.group(1))
    return None


async def handle_owner_private_command(message: Any) -> bool:
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type != "private":
        return False

    command = parse_owner_command(message.text or "")
    if command is None:
        return False

    action, username = command
    if action == "list":
        if not targets:
            await message.answer("Сейчас никого не подкалываю.")
        else:
            names = "\n".join(f"• @{name}" for name in sorted(targets))
            await message.answer(f"Сейчас подкалываю:\n{names}")
        return True

    if action == "start" and username:
        targets.add(username)
        save_targets()
        await message.answer(
            f"😏 Принято. Один раз по-доброму подколю @{username} в общем чате."
        )
        return True

    if action == "stop" and username:
        existed = username in targets
        targets.discard(username)
        playful_users.discard(username)
        save_targets()
        await message.answer(
            f"Больше не подкалываю @{username}."
            if existed else f"@{username} и так не был в списке."
        )
        return True

    return False


TEASES = [
    "{name}, ты это сам придумал или тебе подсказали? 😄",
    "{name}, секундочку, я записываю это в раздел «необъяснимо, но интересно».",
    "{name}, уверенность есть. Осталось найти логику 😏",
    "{name}, я бы ответил серьёзно, но ты слишком хорошо начал.",
    "{name}, смелое заявление. Свидетели есть?",
    "{name}, протокол велит мне уточнить: ты точно в этом уверен?",
    "{name}, я вижу, сегодня скромность взяла выходной.",
    "{name}, это было неожиданно. Даже я на секунду перестал следить за порядком.",
]


async def maybe_tease(message: Any) -> bool:
    if not message.from_user or message.from_user.is_bot:
        return False
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type == "private":
        return False

    username = normalize_username(message.from_user.username or "")
    if not username or username not in targets:
        return False

    display_name = (message.from_user.first_name or f"@{username}").strip()
    await message.reply(random.choice(TEASES).format(name=display_name))
    # Команда одноразовая: после первой шутки убираем цель из очереди.
    targets.discard(username)
    save_targets()
    playful_users.add(username)
    return True


PLAYFUL_DIALOG = [
    "{name}, вот теперь разговариваем 😄 Что хотел сказать?",
    "Ну давай, {name}, удиви меня ещё раз.",
    "{name}, я уже понял: с тобой скучно не будет. Продолжай.",
    "Слушаю, {name}. Только без свидетелей я ничего не обещаю 😏",
    "{name}, хорошо зашёл. О чём поговорим?",
    "Да, {name}, я здесь. На этот раз протокол можно не составлять.",
]


def playful_reply(message: Any) -> str | None:
    if not message.from_user:
        return None
    username = normalize_username(message.from_user.username or "")
    if username not in playful_users or random.random() > 0.55:
        return None
    name = (message.from_user.first_name or f"@{username}").strip()
    return random.choice(PLAYFUL_DIALOG).format(name=name)


def remember_active(message: Any) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    chat_type = getattr(message.chat.type, "value", message.chat.type)
    if chat_type == "private":
        return
    username = normalize_username(message.from_user.username or "")
    key = username or f"id:{message.from_user.id}"
    visible_name = f"@{username}" if username else (message.from_user.first_name or "участник")
    now = time.monotonic()
    members = active_members[message.chat.id]
    members[key] = (now, visible_name)
    for member_key, (stamp, _) in list(members.items()):
        if now - stamp > ACTIVE_MEMBER_SECONDS:
            members.pop(member_key, None)


def maybe_invite_other(message: Any, reply: str) -> str:
    if not message.from_user or random.random() > 0.18:
        return reply
    username = normalize_username(message.from_user.username or "")
    own_key = username or f"id:{message.from_user.id}"
    candidates = [
        name for key, (_, name) in active_members.get(message.chat.id, {}).items()
        if key != own_key
    ]
    if not candidates:
        return reply
    other = random.choice(candidates)
    invitations = [
        f"Кстати, {other}, а ты что думаешь?",
        f"{other}, подключайся к разговору.",
        f"Интересно, что на это скажет {other}."
    ]
    return f"{reply}\n\n{random.choice(invitations)}"
