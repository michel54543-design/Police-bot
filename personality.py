import random
import time


MOODS = ["happy", "sarcastic", "sleepy", "work", "angry_spammers"]
MOOD_INTERVAL_SECONDS = 3 * 60 * 60

current_mood = random.choice(MOODS)
last_mood_change = time.monotonic()


def get_mood() -> str:
    global current_mood
    global last_mood_change

    now = time.monotonic()
    if now - last_mood_change >= MOOD_INTERVAL_SECONDS:
        available = [mood for mood in MOODS if mood != current_mood]
        current_mood = random.choice(available)
        last_mood_change = now
        print(f"Police mood: {current_mood}")

    return current_mood


def style(text: str) -> str:
    mood = get_mood()
    prefix = {
        "happy": "😄 ",
        "sarcastic": "😏 ",
        "sleepy": "😴 ",
        "work": "🛡 ",
        "angry_spammers": "⚔️ ",
    }.get(mood, "")
    return prefix + text
