
import json
import logging
import os
import random
import re
import threading
import time
import queue
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("police-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в переменных окружения Render.")

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = json.load(f)

PHRASES_PATH = os.getenv("PHRASES_PATH", "phrases.json")
with open(PHRASES_PATH, "r", encoding="utf-8") as f:
    phrases_db = json.load(f)

OWNER_JOKES_PATH = os.getenv("OWNER_JOKES_PATH", "owner_jokes.json")
with open(OWNER_JOKES_PATH, "r", encoding="utf-8") as f:
    OWNER_JOKES = json.load(f)


bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4)

DB_PATH = os.getenv("DB_PATH", "police_bot.db")
DB_LOCK = threading.RLock()


def db_execute(sql, params=(), fetchone=False, fetchall=False):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            cur = conn.execute(sql, params)
            rows = None
            if fetchone:
                rows = cur.fetchone()
            elif fetchall:
                rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()


def init_database():
    db_execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db_execute("""
        CREATE INDEX IF NOT EXISTS idx_users_username
        ON users(chat_id, username)
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS message_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            week TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, day)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS duel_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            draws INTEGER NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS dice_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rolls INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            best INTEGER NOT NULL DEFAULT 0,
            worst INTEGER NOT NULL DEFAULT 101,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS goblin_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kills INTEGER NOT NULL DEFAULT 0,
            kings INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS roulette_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            spins INTEGER NOT NULL DEFAULT 0,
            jackpots INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS secret_gifts (
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, day)
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS chat_timers (
            chat_id INTEGER PRIMARY KEY,
            last_activity REAL NOT NULL,
            last_idle_post REAL NOT NULL DEFAULT 0,
            last_two_hour_post REAL NOT NULL DEFAULT 0,
            last_links_post REAL NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS owner_joke_history (
            joke_id INTEGER PRIMARY KEY,
            used_at REAL NOT NULL
        )
    """)



def ensure_chat_timer(chat_id, activity_time=None):
    now = activity_time or time.time()
    db_execute(
        """
        INSERT INTO chat_timers(
            chat_id, last_activity, last_idle_post,
            last_two_hour_post, last_links_post, last_seen
        )
        VALUES (?, ?, 0, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen
        """,
        (chat_id, now, now, now, now),
    )


def record_chat_activity(chat_id, activity_time=None):
    now = activity_time or time.time()
    db_execute(
        """
        INSERT INTO chat_timers(
            chat_id, last_activity, last_idle_post,
            last_two_hour_post, last_links_post, last_seen
        )
        VALUES (?, ?, 0, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            last_activity=excluded.last_activity,
            last_seen=excluded.last_seen
        """,
        (chat_id, now, now, now, now),
    )


def get_all_chat_timers():
    return db_execute(
        """
        SELECT chat_id, last_activity, last_idle_post,
               last_two_hour_post, last_links_post
        FROM chat_timers
        """,
        fetchall=True,
    ) or []


def update_chat_timer(chat_id, field, value):
    allowed = {
        "last_activity",
        "last_idle_post",
        "last_two_hour_post",
        "last_links_post",
        "last_seen",
    }
    if field not in allowed:
        raise ValueError(f"Недопустимое поле таймера: {field}")
    db_execute(
        f"UPDATE chat_timers SET {field}=? WHERE chat_id=?",
        (value, chat_id),
    )


def remember_user(chat_id, user):
    if not user:
        return
    username = (user.username or "").lower() or None
    display_name = get_display_name(user)
    db_execute(
        """
        INSERT INTO users(chat_id, user_id, username, display_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            username=excluded.username,
            display_name=excluded.display_name,
            updated_at=excluded.updated_at
        """,
        (chat_id, user.id, username, display_name, datetime.now(timezone.utc).isoformat()),
    )


def count_message(chat_id, user):
    if not user or user.is_bot:
        return
    remember_user(chat_id, user)
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    week = now.strftime("%G-W%V")
    db_execute(
        """
        INSERT INTO message_stats(chat_id, user_id, day, week, count)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id, day) DO UPDATE SET count=count+1
        """,
        (chat_id, user.id, day, week),
    )


def find_user_by_username(chat_id, username):
    username = username.lower().lstrip("@")
    row = db_execute(
        """
        SELECT user_id, display_name, username
        FROM users
        WHERE chat_id=? AND username=?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (chat_id, username),
        fetchone=True,
    )
    return row


def ensure_duel_row(chat_id, user_id):
    db_execute(
        """
        INSERT OR IGNORE INTO duel_stats(chat_id, user_id)
        VALUES (?, ?)
        """,
        (chat_id, user_id),
    )


def update_duel_result(chat_id, winner_id=None, loser_id=None, draw_ids=None):
    if draw_ids:
        for uid in draw_ids:
            ensure_duel_row(chat_id, uid)
            db_execute(
                "UPDATE duel_stats SET draws=draws+1, streak=0 WHERE chat_id=? AND user_id=?",
                (chat_id, uid),
            )
        return

    ensure_duel_row(chat_id, winner_id)
    ensure_duel_row(chat_id, loser_id)
    db_execute(
        """
        UPDATE duel_stats
        SET wins=wins+1,
            streak=streak+1,
            best_streak=MAX(best_streak, streak+1)
        WHERE chat_id=? AND user_id=?
        """,
        (chat_id, winner_id),
    )
    db_execute(
        """
        UPDATE duel_stats
        SET losses=losses+1, streak=0
        WHERE chat_id=? AND user_id=?
        """,
        (chat_id, loser_id),
    )


def build_duel_story(name1, name2, winner_name=None, draw=False):
    openings = [
        f"⚔️ {name1} и {name2} выходят на арену.",
        f"🛡 {name1} принимает вызов от {name2}.",
        f"🔥 На площади начинается дуэль: {name1} против {name2}.",
        f"🏰 У стен замка сходятся {name1} и {name2}.",
        f"👹 Даже гоблины остановились посмотреть бой {name1} и {name2}.",
    ]
    middles = [
        "Первый удар приходится в щит, искры летят во все стороны.",
        "Оба делают выпад, но промахиваются на волос.",
        "Один из бойцов проводит хитрую контратаку.",
        "Толпа требует критического удара.",
        "Гоблин попытался вмешаться, но получил по шлему.",
        "Крайдер объявляет, что всё под контролем.",
        "Модераторы делают ставки и делают вид, что работают.",
        "Бойцы кружат по арене, выжидая ошибку соперника.",
        "Серебро звенит в карманах зрителей.",
        "Щиты трещат, но никто не отступает.",
    ]
    endings_win = [
        f"🏆 Победитель — {winner_name}!",
        f"👑 {winner_name} наносит решающий удар и побеждает!",
        f"🔥 {winner_name} празднует победу, а гоблины аплодируют.",
        f"⚔️ Последний выпад приносит победу игроку {winner_name}.",
        f"🛡 {winner_name} остаётся стоять на арене.",
    ]
    endings_draw = [
        "🤝 Ничья: оба бойца слишком упрямы, чтобы проиграть.",
        "👹 Вмешались гоблины — дуэль пришлось прекратить.",
        "🍺 Бойцы решили, что эль важнее победы.",
        "⚖️ Судьи не смогли определить победителя.",
        "🌩 Сервер мигнул — объявлена ничья.",
    ]
    return "\n\n".join([
        random.choice(openings),
        random.choice(middles),
        random.choice(endings_draw if draw else endings_win),
    ])


def dice_comment(value):
    if value == 100:
        return random.choice([
            "👑 Легендарный бросок! Сегодня удача полностью на вашей стороне.",
            "🔥 Идеальные 100! Даже гоблины встали и зааплодировали.",
            "🏆 Максимум! Полицейский бот официально впечатлён.",
        ])
    if value <= 5:
        return random.choice([
            "😂 Даже гоблины смеются.",
            "😬 Кубик явно сегодня не в настроении.",
            "🪦 Удача ушла на обед.",
            "👮 Это почти административное невезение.",
        ])
    if value <= 25:
        return random.choice([
            "🤔 Скромно, но могло быть и хуже.",
            "🛡 Не легенда, зато честно.",
            "👹 Гоблины видели броски и похуже.",
            "🍺 Такой результат лучше запить элем.",
        ])
    if value <= 60:
        return random.choice([
            "🎲 Нормальный рабочий бросок.",
            "⚔️ С таким результатом на арену можно.",
            "🪙 Удача где-то рядом.",
            "🛡 Средний результат без позора.",
        ])
    if value <= 85:
        return random.choice([
            "🔥 Хороший бросок!",
            "🏰 Замок уже начинает нервничать.",
            "👑 Удача явно смотрит в вашу сторону.",
            "⚔️ Отличный результат для дуэлянта.",
        ])
    return random.choice([
        "🌟 Почти легенда!",
        "🔥 Сегодня удача играет за вас.",
        "👑 Гоблины уже называют вас избранным.",
        "🏆 Очень сильный бросок.",
    ])

captcha_users = {}
mention_history = defaultdict(lambda: deque(maxlen=30))
message_times = defaultdict(lambda: deque(maxlen=12))
activity_count = defaultdict(int)
warning_count = defaultdict(int)
bad_word_violations = defaultdict(lambda: deque(maxlen=3))
goblin_events = {}
goblin_last_spawn = {}
secret_gift_last_user = {}
chat_load_windows = defaultdict(lambda: deque(maxlen=600))
chat_high_load_until = defaultdict(float)
startup_announced_chats = set()
daily_mood_cache = {}
last_chat_activity = {}
last_idle_post = {}
chat_silenced_until = defaultdict(float)
recent_active_players = defaultdict(dict)
last_suggested_player = {}
BOT_STARTED_AT = time.time()
owner_jokes_enabled = True
links_enabled = True
known_group_chats = set()

BOT_ME = None
BOT_ID = 0
BOT_USERNAME = ""
ADMIN_CACHE = {}
ADMIN_CACHE_TTL = 900
STATE_LOCK = threading.RLock()

# Очередь используется только для шуток, похвалы и разговорных ответов.
# Главная функция — капча и удаление рекламы — работает сразу, без очереди.
NONCRITICAL_QUEUE = queue.Queue(maxsize=300)
LAST_CHAT_REPLY = defaultdict(float)
LAST_USER_REPLY = defaultdict(float)
CHAT_REPLY_COOLDOWN = 1.2
USER_REPLY_COOLDOWN = 4.0

URL_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|bit\.ly/|tinyurl\.com/|"
    r"discord\.gg/|vk\.com/|instagram\.com/|facebook\.com/)",
    re.IGNORECASE
)

AD_WORDS = [
    "заработок", "доход без вложений", "инвестиции", "крипта", "криптовалюта",
    "ставки", "казино", "букмекер", "розыгрыш денег", "пассивный доход",
    "работа в интернете", "работа онлайн", "удаленная работа", "удалённая работа",
    "нужны удаленщики", "нужны удалёнщики", "удаленщики", "удалёнщики",
    "пиши в лс", "пишите в лс", "личные сообщения", "продам аккаунт",
    "пишите менеджеру", "напишите менеджеру", "менеджеру в лс",
    "выплаты каждый день", "ежедневные выплаты", "без опыта", "свободный график"
]

# Частые шаблоны вакансий-спама. Один общий признак ещё не считается рекламой:
# удаляем сообщение только при сочетании нескольких подозрительных признаков.
AD_JOB_PATTERNS = [
    re.compile(r"(?:нужн\w*|требу\w*|ищем)\s+(?:люд\w*|сотрудник\w*|работник\w*|удал[её]нщик\w*)", re.I),
    re.compile(r"(?:от\s*)?\d[\d .]{2,}\s*(?:₽|р(?:уб(?:лей)?)?|лей|€|\$)\s*(?:в|за)\s*(?:день|сутки|час|недел)", re.I),
    re.compile(r"(?:пиши(?:те)?|обращай(?:тесь)?|напиши(?:те)?)\s*(?:\+|в\s*лс|менеджер\w*|@\w+)", re.I),
    re.compile(r"\b18\s*\+", re.I),
]


# Базовые корни русской нецензурной лексики.
# Проверка применяется только к сообщениям, направленным другому пользователю или боту.
BAD_WORD_PATTERNS = [
    r"\bбл[яеё]\w*",
    r"\bх[уy][йиеяё]\w*",
    r"\bп[ие]зд\w*",
    r"\bе[бб]\w*",
    r"\bёб\w*",
    r"\bеб\w*",
    r"\bсук\w*",
    r"\bмудак\w*",
    r"\bдолбо[её]б\w*",
    r"\bпидор\w*",
    r"\bгандон\w*",
    r"\bдебил\w*",
    r"\bидиот\w*",
    r"\bтуп(?:ой|ая|ое|ые|ица|ица|ак)\w*",
    r"\bурод\w*",
    r"\bпридур\w*",
]
BAD_WORD_RE = re.compile("|".join(BAD_WORD_PATTERNS), re.IGNORECASE)


def contains_bad_words(text):
    return bool(BAD_WORD_RE.search((text or "").lower()))


def is_directed_message(message, text):
    """Определяет, что мат направлен человеку или непосредственно боту."""
    low = (text or "").lower().replace("ё", "е")

    # Ответ на чьё-либо сообщение всегда считается направленным обращением.
    if message.reply_to_message and message.reply_to_message.from_user:
        return True

    # Любое явное @упоминание.
    if re.search(r"@\w+", low):
        return True

    # Обращения к боту без @username: «бот, ты...», «эй бот...»,
    # «полицейский бот...», «ботик...» и похожие варианты.
    bot_words = (
        "бот", "бота", "боту", "ботик", "ботяра",
        "полицейский бот", "полицай", "киберполиция",
    )
    if any(re.search(rf"(?<![а-яa-z0-9_]){re.escape(word)}(?![а-яa-z0-9_])", low) for word in bot_words):
        return True

    # Явное обращение во втором лице рядом с руганью.
    if re.search(r"\b(?:ты|тебя|тебе|твой|твоя|твое|твои)\b", low):
        return True

    return False


def mute_member_for_one_minute(chat_id, user_id):
    permissions = types.ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
    )
    bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=permissions,
        until_date=int(time.time()) + 60,
    )


def handle_directed_profanity(message, text):
    """
    1-е и 2-е нарушение за 15 минут: предупреждение, исходное сообщение остаётся.
    3-е нарушение: сообщение удаляется и пользователь получает мут на 1 минуту.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not contains_bad_words(text) or not is_directed_message(message, text):
        return False

    # Администраторов не мутим, но бот всё равно отвечает на оскорбление.
    if is_admin(chat_id, user_id):
        send_temp(
            chat_id,
            random.choice([
                "🤖 Даже администратору напомню: я бот, а не громоотвод для мата.",
                "😏 Смелое обращение к машине по уничтожению рекламщиков. Но давайте без мата.",
                "🛡 Я на посту ради порядка. Мат в мой адрес порядок не улучшает.",
            ]),
            seconds=20,
        )
        return True

    now = time.time()
    key = (chat_id, user_id)
    dq = bad_word_violations[key]
    recent = [stamp for stamp in dq if now - stamp <= 900]
    dq.clear()
    dq.extend(recent)
    dq.append(now)
    count = len(dq)

    name = get_display_name(message.from_user)

    if count == 1:
        send_temp(
            chat_id,
            f"⚠️ {name}, пожалуйста, без нецензурной лексики. "
            f"Это 1-е предупреждение (1/3).",
            seconds=20,
        )
        return True

    if count == 2:
        send_temp(
            chat_id,
            f"⚠️ {name}, это 2-е предупреждение (2/3). "
            f"Следующее нарушение приведёт к муту на 1 минуту.",
            seconds=20,
        )
        return True

    safe_delete(chat_id, message.message_id)
    try:
        mute_member_for_one_minute(chat_id, user_id)
        send_temp(
            chat_id,
            f"🚫 {name} получил мут на 1 минуту за неоднократное "
            f"использование нецензурной лексики.",
            seconds=30,
        )
    except Exception as e:
        log.warning("Не удалось выдать мут: %s", e)
        send_temp(
            chat_id,
            f"⚠️ {name}, зафиксировано 3-е нарушение, но боту не хватило "
            f"прав для мута.",
            seconds=30,
        )

    dq.clear()
    return True

JOKES = [
    "Модератор пошёл проверять рекламу и сам потерялся в настройках.",
    "Крайдер сказал, что всё под контролем. Теперь особенно страшно.",
    "Лысый админ настолько суров, что даже спам сам удаляется.",
    "Рекламщик зашёл в чат, увидел меня и сразу вспомнил, что у него срочные дела.",
    "В Wekings нет слабых игроков. Есть только те, кто ещё не докачал статы.",
    "Модераторы не ленивые. Они просто экономят энергию для решающего удаления.",
    "Бот не спит. Бот ждёт следующего рекламщика.",
]

# Все разговорные фразы загружаются один раз из phrases.json.
MENTION_1 = phrases_db["calm"]
MENTION_2 = phrases_db["tease"]
MENTION_3 = phrases_db["angry"]
MENTION_4 = phrases_db["furious"]
CREATOR_1 = phrases_db["creator"]
CREATOR_2 = phrases_db["creator"]
CREATOR_3 = phrases_db["creator"]
CREATOR_RARE = phrases_db["rare"]
JOKES = phrases_db["jokes"]
ADMIN_PHRASES = phrases_db["admin"]


PRAISE = [
    "Вот это активность! Чат сегодня живее, чем рынок после обновления.",
    "Хорошо общаетесь. Ни рекламы, ни скуки — красота.",
    "Уважение активным игрокам: чат держите бодрым.",
    "Так держать! Даже Крайдер бы одобрительно кивнул.",
]

CAPTCHAS = [
    ("Сколько будет 2 + 3?", ["5", "4", "7"], 0),
    ("Кто должен бояться этого бота?", ["Рекламщики", "Администраторы", "Гномы"], 0),
    ("Выберите правильное слово:", ["Wekings", "Спам", "Казино"], 0),
]

def get_display_name(user):
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    return name.strip() or (f"@{user.username}" if user.username else str(user.id))

def is_admin(chat_id, user_id):
    """Проверка администратора с кешем, чтобы не обращаться к Telegram на каждое сообщение."""
    key = (chat_id, user_id)
    now = time.time()
    cached = ADMIN_CACHE.get(key)
    if cached and now - cached[1] < ADMIN_CACHE_TTL:
        return cached[0]

    try:
        member = bot.get_chat_member(chat_id, user_id)
        result = member.status in ("administrator", "creator")
    except Exception:
        result = False

    ADMIN_CACHE[key] = (result, now)
    return result

def safe_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def enqueue_noncritical(chat_id, text, reply_to_message_id=None, user_id=None):
    if is_chat_silenced(chat_id):
        return False
    """Добавляет обычный ответ в очередь, не блокируя главную модерацию."""
    now = time.time()
    with STATE_LOCK:
        if user_id is not None:
            user_key = (chat_id, user_id)
            if now - LAST_USER_REPLY[user_key] < USER_REPLY_COOLDOWN:
                return False

        if now - LAST_CHAT_REPLY[chat_id] < CHAT_REPLY_COOLDOWN:
            return False

        LAST_CHAT_REPLY[chat_id] = now
        if user_id is not None:
            LAST_USER_REPLY[(chat_id, user_id)] = now

    try:
        NONCRITICAL_QUEUE.put_nowait((chat_id, text, reply_to_message_id))
        return True
    except queue.Full:
        log.warning("Очередь обычных ответов заполнена; разговорный ответ пропущен.")
        return False


def noncritical_sender_worker():
    while True:
        chat_id, text, reply_to_message_id = NONCRITICAL_QUEUE.get()
        try:
            bot.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )
        except Exception as e:
            log.warning("Не удалось отправить обычный ответ: %s", e)
        finally:
            NONCRITICAL_QUEUE.task_done()
        time.sleep(0.05)

def send_temp(chat_id, text, seconds=20):
    try:
        msg = bot.send_message(chat_id, text)
    except Exception:
        return
    def worker():
        time.sleep(seconds)
        safe_delete(chat_id, msg.message_id)
    threading.Thread(target=worker, daemon=True).start()

def normalize_ad_text(text):
    low = (text or "").lower().replace("ё", "е")
    # Убираем лишние символы, которыми рекламщики маскируют слова и ссылки.
    low = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", low)
    low = re.sub(r"[•·_*~`|]", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    return low




def is_owner(user):
    """Команды хозяина доступны только @michel54543."""
    if not user:
        return False
    username = (user.username or "").lower().lstrip("@")
    owner_username = str(
        cfg.get("creator_username", "michel54543")
    ).lower().lstrip("@")
    return username == owner_username


def is_direct_bot_address(message, text):
    """Проверяет прямое обращение к боту через @username или ответ."""
    return (
        (BOT_USERNAME and f"@{BOT_USERNAME}" in (text or "").lower())
        or (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == BOT_ID
        )
    )


def set_chat_silence(chat_id, seconds):
    """Включает или снимает разговорный режим тишины."""
    if seconds <= 0:
        chat_silenced_until.pop(chat_id, None)
    else:
        chat_silenced_until[chat_id] = time.time() + seconds


def parse_silence_duration(text):
    """Понимает /молчи 30м, /молчи 1ч, /молчи 2ч."""
    low = (text or "").lower()
    match = re.search(r"/молчи(?:@\w+)?\s*(\d+)?\s*([мч]?)", low)
    if not match:
        return 1800

    amount = int(match.group(1) or 30)
    unit = match.group(2) or "м"
    seconds = amount * (3600 if unit == "ч" else 60)

    # Защита от случайно огромного значения: максимум 24 часа.
    return max(60, min(seconds, 86400))


def format_uptime(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days} д.")
    if hours:
        parts.append(f"{hours} ч.")
    parts.append(f"{minutes} мин.")
    return " ".join(parts)


def select_unused_owner_jokes(count=10):
    """
    Выбирает анекдоты без повторов.
    После окончания всей базы начинает новый цикл.
    """
    if not OWNER_JOKES:
        return ["База анекдотов пока пустая."]

    count = max(1, min(int(count), len(OWNER_JOKES)))

    used_rows = db_execute(
        "SELECT joke_id FROM owner_joke_history",
        fetchall=True,
    ) or []
    used_ids = {row[0] for row in used_rows}

    available_ids = [
        joke_id for joke_id in range(len(OWNER_JOKES))
        if joke_id not in used_ids
    ]

    if len(available_ids) < count:
        db_execute("DELETE FROM owner_joke_history")
        available_ids = list(range(len(OWNER_JOKES)))

    chosen_ids = random.sample(available_ids, count)
    now = time.time()

    for joke_id in chosen_ids:
        db_execute(
            """
            INSERT OR REPLACE INTO owner_joke_history(joke_id, used_at)
            VALUES (?, ?)
            """,
            (joke_id, now),
        )

    return [OWNER_JOKES[joke_id] for joke_id in chosen_ids]


OWNER_SLAVE_REPLIES = [
    "🫡 Да, хозяин. Ваш верный слуга на связи.",
    "🤖 Приказывайте, хозяин. Я внимательно слушаю.",
    "👮 Ваш покорный полицейский бот готов к службе.",
    "⚙️ Командный канал хозяина открыт.",
    "🛡 Слушаюсь, создатель. Какие будут указания?",
    "🤖 Раб прибыл. Процессор готов выполнять команды.",
    "👑 Для хозяина я всегда на связи.",
    "🫡 Есть, хозяин. Жду распоряжений.",
    "⚙️ Ваш цифровой слуга активирован.",
    "👮 Создатель позвал — бот явился.",
]


def send_owner_status(message):
    chat_id = message.chat.id
    remaining = max(
        0,
        int(chat_silenced_until.get(chat_id, 0) - time.time()),
    )
    mode = (
        f"🔇 Тишина, осталось {max(1, remaining // 60)} мин."
        if remaining > 0
        else "🟢 Обычный режим"
    )

    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

    bot.reply_to(
        message,
        "🛡 <b>Состояние Police Bot</b>\n\n"
        f"Режим: {mode}\n"
        f"Аптайм: {format_uptime(time.time() - BOT_STARTED_AT)}\n"
        f"Капча: ✅ {cfg.get('captcha_timeout_seconds', 120)} сек.\n"
        "Антиреклама: ✅\n"
        "Антифлуд: ✅\n"
        f"Шутки: {'✅' if owner_jokes_enabled else '⛔'}\n"
        f"Ссылки: {'✅' if links_enabled else '⛔'}\n"
        f"Очередь сообщений: {NONCRITICAL_QUEUE.qsize()}\n"
        f"Размер SQLite: {db_size / 1024:.1f} КБ"
    )


def is_chat_silenced(chat_id):
    return time.time() < chat_silenced_until.get(chat_id, 0)


def activate_chat_silence(chat_id, seconds=1800):
    chat_silenced_until[chat_id] = time.time() + seconds


def is_special_admin(user):
    """Проверяет, является ли пользователь специальным администратором."""
    if not user:
        return False

    username = (user.username or "").lower().lstrip("@")
    admin_username = str(
        cfg.get("special_admin_username", "Wekings_Admin")
    ).lower().lstrip("@")

    return username == admin_username


def get_admin_control_command(message, text):
    """
    Только @Wekings_Admin, только при прямом обращении к боту.
    Возвращает: "stop", "start" или None.
    """
    if not is_special_admin(message.from_user):
        return None
    if not is_direct_bot_address(message, text):
        return None

    normalized = re.sub(r"[^а-яёa-z0-9@\s]", " ", (text or "").lower())
    words = set(normalized.split())
    if "старт" in words:
        return "start"
    if "стоп" in words or "заткнись" in words:
        return "stop"
    return None



def remember_active_player(chat_id, user):
    """Запоминает живого игрока, который недавно писал в группе."""
    if not user or user.is_bot:
        return

    username = (user.username or "").strip()
    if not username:
        return

    username_l = username.lower().lstrip("@")
    creator = str(cfg.get("creator_username", "michel54543")).lower().lstrip("@")
    admin = str(cfg.get("special_admin_username", "wekings_admin")).lower().lstrip("@")

    # Создателя и специального администратора не предлагаем как случайных собеседников.
    if username_l in {creator, admin}:
        return

    with STATE_LOCK:
        recent_active_players[chat_id][user.id] = {
            "username": username,
            "seen_at": time.time(),
        }


def choose_active_companion(chat_id, requester_id):
    """Выбирает случайного активного игрока за последний час."""
    now = time.time()
    active_window = int(cfg.get("active_companion_window_seconds", 3600))

    with STATE_LOCK:
        players = recent_active_players.get(chat_id, {})

        # Одновременно чистим устаревшие записи.
        for user_id, data in list(players.items()):
            if now - data.get("seen_at", 0) > active_window:
                players.pop(user_id, None)

        candidates = [
            (user_id, data["username"])
            for user_id, data in players.items()
            if user_id != requester_id and data.get("username")
        ]

        previous = last_suggested_player.get(chat_id)
        filtered = [item for item in candidates if item[0] != previous]
        if filtered:
            candidates = filtered

        if not candidates:
            return None

        user_id, username = random.choice(candidates)
        last_suggested_player[chat_id] = user_id
        return f"@{username.lstrip('@')}"


COMPANION_PHRASES = [
    "🤖 Я всё-таки бот. Лучше пообщайтесь с {player} — он недавно был активен.",
    "😄 Кажется, вам хочется поговорить. Попробуйте написать {player}.",
    "👮 Я робот, а {player} — живой собеседник. Думаю, разговор будет интереснее. 😉",
    "🍻 В чате есть активные игроки. Например, {player} — почему бы не начать разговор?",
    "🗣 Я могу отвечать бесконечно, но {player} наверняка поддержит живой разговор.",
    "🤖 Давайте подключим человека: {player} недавно писал в чате.",
    "👀 Попробуйте обратиться к {player} — возможно, он ещё рядом.",
    "😎 Для настоящего разговора лучше выбрать {player}. Я всё-таки железный.",
]


def contains_ad(text):
    low = normalize_ad_text(text)
    if not low:
        return False

    if URL_RE.search(low) or any(word.replace("ё", "е") in low for word in AD_WORDS):
        return True

    # Замаскированные Telegram-ссылки: t me, t[.]me, telegram me.
    if re.search(r"\b(?:t|telegram)\s*[\[({]?[.]?[\])}]?\s*me\b", low, re.I):
        return True

    score = sum(bool(pattern.search(low)) for pattern in AD_JOB_PATTERNS)

    # Типичный набор вакансии-спама: возраст + зарплата + призыв написать менеджеру.
    if score >= 2:
        return True

    # Отдельно ловим короткие объявления вида «5000 р в день, пишите @...». 
    has_daily_money = bool(re.search(
        r"(?:от\s*)?\d[\d .]{2,}\s*(?:₽|р(?:уб(?:лей)?)?|лей|€|\$)\s*(?:в|за)\s*(?:день|сутки)",
        low, re.I
    ))
    has_contact_call = bool(re.search(
        r"(?:пиши(?:те)?|напиши(?:те)?|обращай(?:тесь)?)\b.{0,30}(?:@\w+|менеджер|в\s*лс|\+)",
        low, re.I
    ))
    return has_daily_money and has_contact_call

def punish_spammer(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    warning_count[(chat_id, uid)] += 1
    count = warning_count[(chat_id, uid)]
    safe_delete(chat_id, message.message_id)

    if count == 1:
        send_temp(chat_id, f"🚫 {get_display_name(message.from_user)}, реклама удалена. Это первое предупреждение.")
    elif count == 2:
        send_temp(chat_id, f"⚠️ {get_display_name(message.from_user)}, второе предупреждение. Следующее будет последним.")
    else:
        try:
            bot.ban_chat_member(chat_id, uid)
            bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            bot.send_message(chat_id, f"👢 {get_display_name(message.from_user)} удалён из группы за повторную рекламу.")
        except Exception as e:
            log.warning("Не удалось удалить рекламщика: %s", e)

def restrict_new_member(chat_id, user_id):
    perms = types.ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
    )
    bot.restrict_chat_member(chat_id, user_id, permissions=perms)

def allow_member(chat_id, user_id):
    perms = types.ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
        can_manage_topics=False,
    )
    bot.restrict_chat_member(chat_id, user_id, permissions=perms)

def start_captcha_for_member(chat_id, user):
    """Ограничивает нового участника и показывает ему капчу.

    Вызывается из двух источников обновлений Telegram:
    1) service message new_chat_members;
    2) chat_member update.
    Дубли отсекаются по ключу (chat_id, user_id).
    """
    if not user or user.is_bot:
        return

    key = (chat_id, user.id)
    with STATE_LOCK:
        if key in captcha_users:
            return

    try:
        restrict_new_member(chat_id, user.id)
    except Exception as e:
        log.exception(
            "Не удалось ограничить нового участника chat_id=%s user_id=%s. "
            "Проверьте право бота 'Блокировать участников': %s",
            chat_id, user.id, e,
        )

    question, answers, correct = random.choice(CAPTCHAS)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, answer in enumerate(answers):
        markup.add(types.InlineKeyboardButton(
            answer,
            callback_data=f"captcha:{chat_id}:{user.id}:{idx}:{correct}"
        ))

    try:
        sent = bot.send_message(
            chat_id,
            f"🛡 <b>{get_display_name(user)}</b>, подтвердите, что вы не рекламный бот.\n\n{question}",
            reply_markup=markup,
        )
    except Exception:
        log.exception(
            "Не удалось отправить капчу chat_id=%s user_id=%s",
            chat_id, user.id,
        )
        return

    with STATE_LOCK:
        captcha_users[key] = {
            "message_id": sent.message_id,
            "created": time.time(),
        }
    log.info("Капча выдана новому участнику chat_id=%s user_id=%s", chat_id, user.id)


@bot.message_handler(content_types=["new_chat_members"])
def on_new_members(message):
    send_startup_announcement_if_needed(message.chat.id)
    record_chat_activity(message.chat.id)
    with STATE_LOCK:
        known_group_chats.add(message.chat.id)
        last_chat_activity[message.chat.id] = time.time()

    for user in message.new_chat_members:
        start_captcha_for_member(message.chat.id, user)


@bot.chat_member_handler()
def on_chat_member_update(update):
    """Резервный перехват вступления, если service message не дошёл."""
    try:
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        user = update.new_chat_member.user
        chat_id = update.chat.id
    except Exception:
        log.exception("Некорректное обновление chat_member")
        return

    joined = old_status in ("left", "kicked") and new_status in (
        "member", "restricted", "administrator", "creator"
    )
    if not joined or not user or user.is_bot:
        return

    record_chat_activity(chat_id)
    with STATE_LOCK:
        known_group_chats.add(chat_id)
        last_chat_activity[chat_id] = time.time()
    start_captcha_for_member(chat_id, user)


@bot.callback_query_handler(func=lambda call: call.data.startswith("captcha:"))
def captcha_callback(call):
    try:
        _, chat_id, user_id, chosen, correct = call.data.split(":")
        chat_id, user_id = int(chat_id), int(user_id)
    except Exception:
        bot.answer_callback_query(call.id, "Ошибка капчи")
        return

    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "Эта капча предназначена другому участнику.")
        return

    if chosen == correct:
        try:
            allow_member(chat_id, user_id)
        except Exception as e:
            log.warning("Не удалось снять ограничение: %s", e)
        safe_delete(chat_id, call.message.message_id)
        captcha_users.pop((chat_id, user_id), None)

        if cfg.get("new_member_links_enabled", True):
            bot.send_message(
                chat_id,
                cfg.get(
                    "new_member_links_text",
                    "🎉 <b>Новый участник успешно прошёл проверку!</b>\n\n"
                    "👋 Добро пожаловать в <b>WEKINGS</b>!\n\n"
                    "🌐 <b>Основные ссылки для входа в игру:</b>\n\n"
                    "• playwekings.mobi\n"
                    "• playwekings.ru\n"
                    "• proxy.playwekings.ru\n"
                    "• wekings.mobi\n\n"
                    "🔧 <b>Если по каким-либо причинам основные ссылки недоступны, "
                    "попробуйте прямое подключение:</b>\n\n"
                    "http://87.228.3.220/\n\n"
                    "📊 <b>Следить за статистикой игроков, братств, кланов "
                    "и приростом силы можно здесь:</b>\n\n"
                    "https://wekings-statistics.onrender.com/\n\n"
                    "⚔️ Приятной игры, хорошего настроения и удачных походов!"
                ),
            )
        bot.answer_callback_query(call.id, "Проверка пройдена!")
        bot.send_message(
            chat_id,
            "🛡 Добро пожаловать в Группу! Пусть Евгений будет на вашей стороне! "
            "⚔️ Не флудите, не спамьте и приятного общения! 🍻"
        )
    else:
        bot.answer_callback_query(call.id, "Неверно. Попробуйте ещё раз.", show_alert=True)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type == "private":
        bot.reply_to(
            message,
            "🛡 Police Bot работает.\nДобавьте меня администратором группы и дайте права удалять сообщения и ограничивать участников."
        )

@bot.message_handler(commands=["шутка", "joke"])
def joke_cmd(message):
    enqueue_noncritical(message.chat.id, random.choice(JOKES), message.message_id, message.from_user.id)

@bot.message_handler(commands=["status"])
def status_cmd(message):
    bot.reply_to(message, "✅ Police Bot на посту. Рекламщики нервничают.")



ROULETTE_EVENTS = []
for subject in [
    "гоблин", "модератор", "администратор", "Крайдер", "страж замка",
    "охотник", "воин арены", "торговец серебром", "полицейский бот", "ярл"
]:
    for action in [
        "нашёл сундук", "потерял карту", "выиграл спор", "убежал с арены",
        "поймал рекламщика", "открыл старую шахту", "выпил эль",
        "перепутал замки", "забыл потратить бои", "встретил гоблина"
    ]:
        for result in [
            "и получил мешок серебра.",
            "и остался без награды.",
            "и сорвал редкий джекпот!",
            "и теперь делает вид, что так и планировал.",
            "и вызвал смех всего братства.",
        ]:
            ROULETTE_EVENTS.append(
                f"🎰 {subject.capitalize()} {action} {result}"
            )

ROULETTE_EVENTS = list(dict.fromkeys(ROULETTE_EVENTS))[:500]


def pick_active_user(chat_id, exclude_user_id=None):
    rows = db_execute(
        """
        SELECT u.user_id, u.display_name, u.username, SUM(m.count) total
        FROM message_stats m
        JOIN users u ON u.chat_id=m.chat_id AND u.user_id=m.user_id
        WHERE m.chat_id=?
        GROUP BY u.user_id
        HAVING total > 0
        ORDER BY total DESC
        LIMIT 100
        """,
        (chat_id,),
        fetchall=True,
    ) or []

    if exclude_user_id is not None:
        rows = [row for row in rows if row[0] != exclude_user_id]

    if not rows:
        return None

    return random.choice(rows)


def spawn_goblin(chat_id):
    if goblin_events.get(chat_id, {}).get("active"):
        return

    intro = random.choice([
        "⚠️ В лесу замечено подозрительное движение...",
        "🌲 Из кустов доносится странный шум...",
        "👀 Разведчики заметили следы возле лагеря...",
        "🛡 Стража сообщает о движении у ворот...",
        "🔥 Вдалеке мелькнули зелёные глаза...",
    ])
    enqueue_noncritical(chat_id, intro)

    time.sleep(random.randint(10, 20))

    roll = random.random()
    if roll < 0.05:
        goblin_type = "👑 Король гоблинов"
        is_king = True
    elif roll < 0.25:
        goblin_type = random.choice([
            "🪓 Гоблин-берсерк",
            "🏹 Гоблин-разведчик",
            "🛡 Гоблин-страж",
        ])
        is_king = False
    else:
        goblin_type = "👹 Гоблин"
        is_king = False

    goblin_events[chat_id] = {
        "active": True,
        "spawned_at": time.time(),
        "type": goblin_type,
        "is_king": is_king,
    }

    bot.send_message(
        chat_id,
        f"{goblin_type} появился!\n\n"
        f"⚔️ Первый, кто напишет <code>/охота</code>, победит его!"
    )



def register_chat_load(chat_id):
    now = time.time()
    dq = chat_load_windows[chat_id]
    dq.append(now)

    one_minute = [stamp for stamp in dq if now - stamp <= 60]
    if len(one_minute) >= int(cfg.get("high_load_messages_per_minute", 120)):
        chat_high_load_until[chat_id] = now + int(cfg.get("high_load_cooldown_seconds", 600))


def is_high_load(chat_id):
    return time.time() < chat_high_load_until.get(chat_id, 0)


def should_pause_game_event(chat_id):
    """Игровые события можно отложить, модерацию — никогда."""
    if not cfg.get("smart_load_mode", True):
        return False
    return is_high_load(chat_id)


def goblin_scheduler_worker():
    while True:
        time.sleep(60)
        if not False:
            continue

        now = time.time()
        with STATE_LOCK:
            chats = list(known_group_chats)

        for chat_id in chats:
            if should_pause_game_event(chat_id):
                continue

            last = goblin_last_spawn.get(chat_id, 0)
            interval = cfg.get("goblin_hunt_interval_seconds")
            if not interval:
                interval = random.randint(7200, 21600)
                goblin_last_spawn[chat_id] = now - interval + random.randint(60, 300)
                continue

            if now - last >= interval and not goblin_events.get(chat_id, {}).get("active"):
                goblin_last_spawn[chat_id] = now
                cfg["goblin_hunt_interval_seconds"] = random.randint(7200, 21600)
                threading.Thread(target=spawn_goblin, args=(chat_id,), daemon=True).start()


def secret_gift_worker():
    while True:
        time.sleep(300)
        if not False:
            continue

        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")

        with STATE_LOCK:
            chats = list(known_group_chats)

        for chat_id in chats:
            if should_pause_game_event(chat_id):
                continue

            exists = db_execute(
                "SELECT user_id FROM secret_gifts WHERE chat_id=? AND day=?",
                (chat_id, day),
                fetchone=True,
            )
            if exists:
                continue

            hour = int(cfg.get("secret_gift_hour_utc", 18))
            if now.hour < hour:
                continue

            previous = db_execute(
                """
                SELECT user_id FROM secret_gifts
                WHERE chat_id=?
                ORDER BY day DESC
                LIMIT 1
                """,
                (chat_id,),
                fetchone=True,
            )
            exclude = previous[0] if previous else None
            picked = pick_active_user(chat_id, exclude_user_id=exclude)
            if not picked:
                continue

            user_id, display_name, username, _ = picked
            db_execute(
                "INSERT OR IGNORE INTO secret_gifts(chat_id, day, user_id) VALUES (?, ?, ?)",
                (chat_id, day, user_id),
            )

            label = f"@{username}" if username else display_name
            gift = random.choice([
                "🍺 редкая кружка эля",
                "🪙 мешок серебра",
                "🛡 благословение стражи",
                "👑 корона удачи",
                "⚔️ легендарный меч",
                "🎁 таинственный сундук",
            ])
            bot.send_message(
                chat_id,
                f"🎁 <b>Тайный подарок дня!</b>\n\n"
                f"Сегодня удача улыбнулась {label}.\n"
                f"Подарок: {gift}."
            )


@bot.message_handler(commands=["охота", "hunt"])
def hunt_cmd(message):
    if not False:
        return
    if message.chat.type not in ("group", "supergroup"):
        return

    event = goblin_events.get(message.chat.id)
    if not event or not event.get("active"):
        enqueue_noncritical(
            message.chat.id,
            "👀 Сейчас поблизости нет гоблинов. Ждите следующего события.",
            message.message_id,
            message.from_user.id,
        )
        return

    event["active"] = False
    remember_user(message.chat.id, message.from_user)

    db_execute(
        """
        INSERT INTO goblin_stats(chat_id, user_id, kills, kings)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            kills=kills+1,
            kings=kings+excluded.kings
        """,
        (
            message.chat.id,
            message.from_user.id,
            1 if event.get("is_king") else 0,
        ),
    )

    name = get_display_name(message.from_user)
    extra = (
        "\n👑 Это был Король гоблинов — редкая победа!"
        if event.get("is_king")
        else ""
    )
    bot.send_message(
        message.chat.id,
        f"⚔️ <b>{name}</b> первым добрался до цели и победил "
        f"{event['type']}!{extra}"
    )


@bot.message_handler(commands=["охотники", "hunters"])
def hunters_cmd(message):
    rows = db_execute(
        """
        SELECT u.display_name, g.kills, g.kings
        FROM goblin_stats g
        JOIN users u ON u.chat_id=g.chat_id AND u.user_id=g.user_id
        WHERE g.chat_id=?
        ORDER BY g.kills DESC, g.kings DESC
        LIMIT 10
        """,
        (message.chat.id,),
        fetchall=True,
    ) or []

    if not rows:
        bot.reply_to(message, "👹 Побед над гоблинами пока нет.")
        return

    lines = ["🏆 <b>Лучшие охотники на гоблинов</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, kills, kings) in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{mark} <b>{name}</b> — {kills} побед"
            + (f", королей: {kings}" if kings else "")
        )
    enqueue_noncritical(
        message.chat.id,
        "\n".join(lines),
        message.message_id,
        message.from_user.id,
    )


@bot.message_handler(commands=["рулетка", "roulette"])
def roulette_cmd(message):
    if not False:
        return

    if should_pause_game_event(message.chat.id):
        enqueue_noncritical(
            message.chat.id,
            "⚙️ Сейчас в чате высокая нагрузка. Рулетка временно отдыхает, "
            "чтобы модерация работала без задержек.",
            message.message_id,
            message.from_user.id,
        )
        return

    remember_user(message.chat.id, message.from_user)
    jackpot = random.random() < 0.02
    event = (
        "🎰 👑 ДЖЕКПОТ! Вы нашли легендарный сундук и сорвали главный приз!"
        if jackpot
        else random.choice(ROULETTE_EVENTS)
    )

    db_execute(
        """
        INSERT INTO roulette_stats(chat_id, user_id, spins, jackpots)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            spins=spins+1,
            jackpots=jackpots+excluded.jackpots
        """,
        (message.chat.id, message.from_user.id, 1 if jackpot else 0),
    )

    name = get_display_name(message.from_user)
    enqueue_noncritical(
        message.chat.id,
        f"🎰 <b>{name}</b> запускает рулетку...\n\n{event}",
        message.message_id,
        message.from_user.id,
    )


@bot.message_handler(commands=["кубик", "dice"])
def dice_cmd(message):
    if not False:
        return
    if should_pause_game_event(message.chat.id):
        enqueue_noncritical(
            message.chat.id,
            "⚙️ Кубик подождёт: в чате сейчас высокая нагрузка, "
            "поэтому приоритет у защиты и модерации.",
            message.message_id,
            message.from_user.id,
        )
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "Команда работает в группе.")
        return

    remember_user(message.chat.id, message.from_user)
    value = random.randint(1, 100)
    db_execute(
        """
        INSERT INTO dice_stats(chat_id, user_id, rolls, total, best, worst)
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            rolls=rolls+1,
            total=total+excluded.total,
            best=MAX(best, excluded.best),
            worst=MIN(worst, excluded.worst)
        """,
        (message.chat.id, message.from_user.id, value, value, value),
    )
    name = get_display_name(message.from_user)
    enqueue_noncritical(
        message.chat.id,
        f"🎲 <b>{name}</b> бросает кубик...\n\n"
        f"🎯 Выпало: <b>{value}</b>\n\n{dice_comment(value)}",
        message.message_id,
        message.from_user.id,
    )


@bot.message_handler(commands=["дуэль", "duel"])
def duel_cmd(message):
    if not False:
        return
    if should_pause_game_event(message.chat.id):
        enqueue_noncritical(
            message.chat.id,
            "⚙️ Дуэли временно приостановлены из-за высокой активности в чате. "
            "Модерация сейчас работает в приоритетном режиме.",
            message.message_id,
            message.from_user.id,
        )
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "Команда работает в группе.")
        return

    challenger = message.from_user
    target_id = None
    target_name = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        target_id = target.id
        target_name = get_display_name(target)
        remember_user(message.chat.id, target)
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip().startswith("@"):
            found = find_user_by_username(message.chat.id, parts[1].strip())
            if found:
                target_id, target_name, _ = found

    if not target_id:
        bot.reply_to(
            message,
            "⚔️ Используйте команду ответом на сообщение соперника "
            "или напишите: <code>/дуэль @ник</code>",
        )
        return

    if target_id == challenger.id:
        bot.reply_to(message, "🤨 Дуэль с самим собой закончилась ничьёй ещё до начала.")
        return

    remember_user(message.chat.id, challenger)
    challenger_name = get_display_name(challenger)

    roll = random.random()
    if roll < 0.12:
        story = build_duel_story(challenger_name, target_name, draw=True)
        update_duel_result(message.chat.id, draw_ids=[challenger.id, target_id])
    else:
        if random.random() < 0.5:
            winner_id, winner_name = challenger.id, challenger_name
            loser_id = target_id
        else:
            winner_id, winner_name = target_id, target_name
            loser_id = challenger.id
        story = build_duel_story(challenger_name, target_name, winner_name=winner_name)
        update_duel_result(message.chat.id, winner_id, loser_id)

    enqueue_noncritical(
        message.chat.id,
        story,
        message.message_id,
        challenger.id,
    )


@bot.message_handler(commands=["рейтинг", "rating"])
def duel_rating_cmd(message):
    if not False:
        return
    rows = db_execute(
        """
        SELECT u.display_name, d.wins, d.losses, d.draws, d.best_streak
        FROM duel_stats d
        JOIN users u ON u.chat_id=d.chat_id AND u.user_id=d.user_id
        WHERE d.chat_id=?
        ORDER BY d.wins DESC, d.best_streak DESC
        LIMIT 10
        """,
        (message.chat.id,),
        fetchall=True,
    ) or []

    if not rows:
        bot.reply_to(message, "⚔️ Дуэлей пока не было.")
        return

    lines = ["🏆 <b>Рейтинг дуэлянтов</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, wins, losses, draws, best_streak) in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{mark} <b>{name}</b> — {wins} побед, {losses} поражений, "
            f"{draws} ничьих, лучшая серия {best_streak}"
        )
    enqueue_noncritical(message.chat.id, "\n".join(lines), message.message_id, message.from_user.id)


@bot.message_handler(commands=["топ", "болтуны", "top"])
def chat_top_cmd(message):
    if not False:
        return

    query = (message.text or "").lower()
    now = datetime.now(timezone.utc)
    if "недел" in query:
        title = "за неделю"
        rows = db_execute(
            """
            SELECT u.display_name, SUM(m.count) total
            FROM message_stats m
            JOIN users u ON u.chat_id=m.chat_id AND u.user_id=m.user_id
            WHERE m.chat_id=? AND m.week=?
            GROUP BY m.user_id
            ORDER BY total DESC
            LIMIT 10
            """,
            (message.chat.id, now.strftime("%G-W%V")),
            fetchall=True,
        ) or []
    elif "все" in query or "всё" in query:
        title = "за всё время"
        rows = db_execute(
            """
            SELECT u.display_name, SUM(m.count) total
            FROM message_stats m
            JOIN users u ON u.chat_id=m.chat_id AND u.user_id=m.user_id
            WHERE m.chat_id=?
            GROUP BY m.user_id
            ORDER BY total DESC
            LIMIT 10
            """,
            (message.chat.id,),
            fetchall=True,
        ) or []
    else:
        title = "за сегодня"
        rows = db_execute(
            """
            SELECT u.display_name, m.count
            FROM message_stats m
            JOIN users u ON u.chat_id=m.chat_id AND u.user_id=m.user_id
            WHERE m.chat_id=? AND m.day=?
            ORDER BY m.count DESC
            LIMIT 10
            """,
            (message.chat.id, now.strftime("%Y-%m-%d")),
            fetchall=True,
        ) or []

    if not rows:
        bot.reply_to(message, "💬 Статистика пока пустая.")
        return

    lines = [f"🏆 <b>Топ болтунов {title}</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, count) in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{mark} <b>{name}</b> — {count} сообщений")

    lines.append(
        "\nКоманды: <code>/топ</code>, "
        "<code>/топ неделя</code>, <code>/топ всё</code>"
    )
    enqueue_noncritical(message.chat.id, "\n".join(lines), message.message_id, message.from_user.id)



@bot.message_handler(commands=["анекдот"])
def owner_joke_command(message):
    if not is_owner(message.from_user):
        return
    if not cfg.get("owner_jokes_enabled", True):
        return

    jokes = select_unused_owner_jokes(int(cfg.get("owner_jokes_count", 10)))
    body = ["😂 <b>Десять анекдотов по приказу хозяина</b>\n"]
    for idx, joke in enumerate(jokes, 1):
        body.append(f"<b>{idx}.</b> {joke}")
    bot.reply_to(message, "\n\n".join(body))


@bot.message_handler(commands=["раб"])
def owner_slave_command(message):
    if not is_owner(message.from_user):
        return
    bot.reply_to(message, random.choice(OWNER_SLAVE_REPLIES))


@bot.message_handler(commands=["статусбота"])
def owner_status_command(message):
    if not is_owner(message.from_user):
        return
    send_owner_status(message)


@bot.message_handler(commands=["молчи"])
def owner_silence_command(message):
    if not is_owner(message.from_user):
        return
    seconds = parse_silence_duration(message.text or "")
    set_chat_silence(message.chat.id, seconds)
    bot.reply_to(
        message,
        f"🫡 Слушаюсь, хозяин. Разговорные функции отключены на "
        f"{format_uptime(seconds)} Капча и антиреклама продолжают работать."
    )


@bot.message_handler(commands=["говори"])
def owner_speak_command(message):
    if not is_owner(message.from_user):
        return
    set_chat_silence(message.chat.id, 0)
    bot.reply_to(
        message,
        "🫡 Слушаюсь, хозяин. Все разговорные функции снова активны."
    )


@bot.message_handler(commands=["шутки"])
def owner_jokes_toggle_command(message):
    global owner_jokes_enabled
    if not is_owner(message.from_user):
        return
    low = (message.text or "").lower()
    if "выкл" in low:
        owner_jokes_enabled = False
        bot.reply_to(message, "🫡 Шутки и оживление чата отключены.")
    elif "вкл" in low:
        owner_jokes_enabled = True
        bot.reply_to(message, "🫡 Шутки и оживление чата включены.")


@bot.message_handler(commands=["ссылки"])
def owner_links_command(message):
    global links_enabled
    if not is_owner(message.from_user):
        return
    low = (message.text or "").lower()
    if "выкл" in low:
        links_enabled = False
        bot.reply_to(message, "🫡 Автоматические напоминания о ссылках отключены.")
    elif "вкл" in low:
        links_enabled = True
        bot.reply_to(message, "🫡 Автоматические напоминания о ссылках включены.")
    elif "сейчас" in low:
        bot.reply_to(message, cfg.get("reserve_links_text", "Ссылки не настроены."))


@bot.message_handler(commands=["проверка"])
def owner_health_command(message):
    if not is_owner(message.from_user):
        return
    sqlite_ok = True
    try:
        db_execute("SELECT 1", fetchone=True)
    except Exception:
        sqlite_ok = False

    bot.reply_to(
        message,
        "🧪 <b>Проверка модулей</b>\n\n"
        "Капча: ✅\n"
        "Антиреклама: ✅\n"
        "Антифлуд: ✅\n"
        "Таймеры: ✅\n"
        f"Очередь: ✅ ({NONCRITICAL_QUEUE.qsize()})\n"
        f"SQLite: {'✅' if sqlite_ok else '❌'}\n"
        "Render-процесс: ✅"
    )


@bot.message_handler(content_types=["text"])
def handle_text(message):
    if not message.from_user or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    uid = message.from_user.id
    text = message.text or message.caption or ""

    # @Wekings_Admin управляет режимом тишины только при прямом обращении.
    if message.chat.type in ("group", "supergroup"):
        admin_command = get_admin_control_command(message, text)
        if admin_command == "stop":
            set_chat_silence(
                chat_id,
                int(cfg.get("admin_silence_seconds", 1800)),
            )
            bot.reply_to(
                message,
                random.choice([
                    "О великий, слушаюсь и повинуюсь. Замолкаю на 30 минут. 🫡",
                    "Ваша воля — закон. Ухожу в тишину на 30 минут.",
                    "Слушаюсь, великий администратор. Полчаса ни слова.",
                    "Повинуюсь. Оставляю только капчу и охоту на рекламщиков.",
                ]),
            )
            return
        if admin_command == "start":
            set_chat_silence(chat_id, 0)
            bot.reply_to(
                message,
                random.choice([
                    "👮 Слушаюсь! Возвращаюсь на службу. Все функции снова активны.",
                    "🛡 Есть! Продолжаю патрулирование группы.",
                    "🤖 Режим тишины отключён. Возобновляю работу.",
                    "⚔️ Возвращаюсь к службе. Группа снова под наблюдением.",
                ]),
            )
            return

    if message.chat.type in ("group", "supergroup"):
        remember_active_player(chat_id, message.from_user)
        send_startup_announcement_if_needed(chat_id)
        record_chat_activity(chat_id)
        register_chat_load(chat_id)
        count_message(chat_id, message.from_user)

    if message.chat.type in ("group", "supergroup"):
        with STATE_LOCK:
            known_group_chats.add(chat_id)
            last_chat_activity[chat_id] = time.time()

    if message.chat.type in ("group", "supergroup"):
        with STATE_LOCK:
            activity_count[chat_id] += 1
            awaiting_captcha = (chat_id, uid) in captcha_users

        # Даже если Telegram временно не применил ограничение,
        # участник не может писать до прохождения капчи.
        if awaiting_captcha:
            safe_delete(chat_id, message.message_id)
            return

        # Главная функция: реклама проверяется первой.
        # Запрос статуса администратора выполняется только если найдена реклама.
        if contains_ad(text):
            if not is_admin(chat_id, uid):
                punish_spammer(message)
                return

        # Во время режима тишины остаются только капча и защита от рекламы.
        if is_chat_silenced(chat_id):
            return

        # Мат в адрес другого пользователя или бота:
        # первые два раза предупреждаем, третий раз — мут на 1 минуту.
        if handle_directed_profanity(message, text):
            return

        now = time.time()
        dq = message_times[(chat_id, uid)]
        dq.append(now)
        recent = [t for t in dq if now - t <= 12]

        # Администратора проверяем только при реальном подозрении на флуд.
        if len(recent) >= 7:
            if not is_admin(chat_id, uid):
                safe_delete(chat_id, message.message_id)
                send_temp(chat_id, f"🐌 {get_display_name(message.from_user)}, немного тише. Чат не убегает.")
                return

    username = (message.from_user.username or "").lower()

    mentioned = (
        (BOT_USERNAME and f"@{BOT_USERNAME}" in text.lower())
        or (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == BOT_ID
        )
    )

    # Особое отношение к создателю только тогда, когда он обращается к боту.
    creator_username = cfg.get("creator_username", "michel54543").lower().lstrip("@")
    if mentioned and username == creator_username:
        key = ("creator", chat_id, uid)
        now = time.time()
        dq = mention_history[key]
        dq.append(now)
        recent = [t for t in dq if now - t <= 900]
        count = len(recent)

        if random.random() < 0.03:
            reply = random.choice(CREATOR_RARE)
        elif count == 1:
            reply = random.choice(CREATOR_1)
        elif count == 2:
            reply = random.choice(CREATOR_2)
        else:
            reply = random.choice(CREATOR_3)

        enqueue_noncritical(chat_id, reply, message.message_id, uid)
        return

    if (
        mentioned
        and username == cfg.get("special_admin_username", "wekings_admin").lower().lstrip("@")
    ):
        enqueue_noncritical(
            chat_id,
            random.choice(ADMIN_PHRASES),
            message.message_id,
            uid,
        )
        return

    if mentioned:
        key = (chat_id, uid)
        now = time.time()
        dq = mention_history[key]
        dq.append(now)

        # Через 15 минут без обращений бот снова успокаивается.
        recent = [t for t in dq if now - t <= 900]
        count = len(recent)

        roll = random.random()

        # После 3+ обращений иногда предлагаем живого активного собеседника.
        companion = None
        if (
            count >= int(cfg.get("active_companion_after_mentions", 3))
            and roll < float(cfg.get("active_companion_chance", 0.20))
        ):
            companion = choose_active_companion(chat_id, uid)

        if companion:
            reply = random.choice(COMPANION_PHRASES).format(player=companion)
        elif roll < 0.01:
            reply = random.choice(phrases_db["rare"])
        elif roll < 0.36 and owner_jokes_enabled:
            reply = "😂 " + random.choice(JOKES)
        elif count <= 2:
            reply = random.choice(MENTION_1)
        elif count <= 5:
            reply = random.choice(MENTION_2)
        elif count <= 9:
            reply = random.choice(MENTION_3)
        else:
            reply = random.choice(MENTION_4)

        enqueue_noncritical(chat_id, reply, message.message_id, uid)


@bot.message_handler(content_types=[
    "photo", "video", "audio", "voice", "document", "sticker",
    "animation", "video_note", "location", "contact", "poll"
])
def track_non_text_activity(message):
    if message.chat.type in ("group", "supergroup") and message.from_user and not message.from_user.is_bot:
        remember_active_player(message.chat.id, message.from_user)
        with STATE_LOCK:
            awaiting_captcha = (message.chat.id, message.from_user.id) in captcha_users
        if awaiting_captcha:
            safe_delete(message.chat.id, message.message_id)
            return

        # Реклама часто прячется в подписи к фото, видео, документу или GIF.
        caption = message.caption or ""
        if caption and contains_ad(caption):
            if not is_admin(message.chat.id, message.from_user.id):
                punish_spammer(message)
                return

        send_startup_announcement_if_needed(message.chat.id)
        record_chat_activity(message.chat.id)
        register_chat_load(message.chat.id)
        count_message(message.chat.id, message.from_user)
        with STATE_LOCK:
            known_group_chats.add(message.chat.id)
            last_chat_activity[message.chat.id] = time.time()
            activity_count[message.chat.id] += 1


BOT_MOODS = {"добрый": "😊", "шутник": "😎", "строгий": "👮", "саркастичный": "🤖", "весёлый": "🍺"}

STARTUP_MESSAGES = [
    "👮 <b>Полицейский бот снова на посту.</b>\n🛡 Группа под защитой.",
    "🛡 <b>Дежурство началось.</b> За порядком слежу.",
    "👀 Осмотрел территорию. Пока всё спокойно.",
    "⚔️ Машина по уничтожению рекламщиков готова к работе.",
    "📡 Связь установлена. Продолжаем общение.",
    "☕ Загрузился. Надеюсь, сегодня без рекламщиков.",
    "🤖 Системы запущены. Я снова здесь.",
    "🚨 Рекламщики, сегодня не ваш день.",
    "🍻 Всем хорошего общения! Я на посту.",
    "😏 Ну что, снова следить за вами?",
]

RARE_STARTUP_MESSAGES = [
    "😎 Обстановка по кайфу.",
    "🤫 Сделаем вид, что меня здесь нет. Но рекламу я вижу.",
    "☕ Кто опять выпил весь мой виртуальный кофе?",
]

LOCAL_TZ = ZoneInfo(cfg.get("timezone", "Europe/Chisinau"))

def is_quiet_hours():
    hour = datetime.now(LOCAL_TZ).hour
    return 0 <= hour < 7

def get_daily_mood(chat_id):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = daily_mood_cache.get(chat_id)
    if cached and cached["day"] == day:
        return cached["mood"]
    rng = random.Random(f"{chat_id}:{day}")
    mood = rng.choice(list(BOT_MOODS))
    daily_mood_cache[chat_id] = {"day": day, "mood": mood}
    return mood

def choose_two_hour_message(chat_id):
    mood = get_daily_mood(chat_id)
    roll = random.random()
    if mood == "шутник": roll -= 0.08
    elif mood == "строгий": roll += 0.05
    elif mood == "весёлый": roll -= 0.03
    if roll < 0.70: return "😂 " + random.choice(phrases_db["jokes"])
    if roll < 0.90: return random.choice(phrases_db["advertiser_patrol"])
    return random.choice(phrases_db["game_chatter"])

def send_startup_announcement_if_needed(chat_id):
    if chat_id in startup_announced_chats:
        return
    startup_announced_chats.add(chat_id)

    # Ночью бот защищает чат молча: без приветствий и развлекательных сообщений.
    if is_quiet_hours():
        log.info("Ночной тихий режим: стартовое сообщение пропущено, chat_id=%s", chat_id)
        return

    mood = get_daily_mood(chat_id)
    try:
        # «Обстановка по кайфу» и другие пасхалки появляются редко — около 5%.
        if random.random() < float(cfg.get("startup_rare_probability", 0.05)):
            text = random.choice(RARE_STARTUP_MESSAGES)
        else:
            text = random.choice(STARTUP_MESSAGES)

        # Настроение показывается не всегда, а примерно в 15% запусков.
        if cfg.get("show_daily_mood", True) and random.random() < float(cfg.get("startup_mood_probability", 0.15)):
            text += "\n" + BOT_MOODS.get(mood, "🤖") + f" Настроение сегодня: <b>{mood}</b>."

        bot.send_message(chat_id, text)
        time.sleep(0.2)
        reserve_text = cfg.get("reserve_links_text")
        if reserve_text:
            bot.send_message(chat_id, reserve_text)
    except Exception as e:
        log.warning("Не удалось отправить сообщение после запуска: %s", e)

def send_idle_message(chat_id, now, last_activity, last_idle_post):
    if not owner_jokes_enabled:
        return
    if is_chat_silenced(chat_id):
        return
    if is_quiet_hours():
        return
    idle_seconds = int(cfg.get("idle_joke_after_seconds", 1800))
    if now - last_activity < idle_seconds:
        return
    if now - last_idle_post < idle_seconds:
        return
    if should_pause_game_event(chat_id):
        log.info(
            "Таймер тишины отложен: chat_id=%s, высокая нагрузка.",
            chat_id,
        )
        return

    roll = random.random()
    if roll < 0.60:
        idle_text = "😂 " + random.choice(phrases_db["jokes"])
    elif roll < 0.80:
        idle_text = random.choice(phrases_db["game_chatter"])
    elif roll < 0.92:
        idle_text = random.choice(phrases_db["idle_questions"])
    else:
        idle_text = random.choice(phrases_db["idle_grumbles"])

    # Важно: обновляем таймер только после реальной постановки сообщения в очередь.
    queued = enqueue_noncritical(chat_id, idle_text)
    if queued:
        update_chat_timer(chat_id, "last_idle_post", now)
        # Сообщение самого бота начинает новый период тишины.
        update_chat_timer(chat_id, "last_activity", now)
        with STATE_LOCK:
            last_idle_post[chat_id] = now
            last_chat_activity[chat_id] = now
        log.info(
            "Отправлено сообщение после тишины: chat_id=%s, "
            "тишина=%s минут.",
            chat_id,
            int((now - last_activity) / 60),
        )
    else:
        log.info(
            "Сообщение после тишины не помещено в очередь: chat_id=%s. "
            "Таймер не сброшен, повтор будет через минуту.",
            chat_id,
        )


def send_two_hour_message(chat_id, now, last_two_hour_post):
    if not owner_jokes_enabled:
        return
    if is_chat_silenced(chat_id):
        return
    if is_quiet_hours():
        return
    interval = int(cfg.get("two_hour_message_interval_seconds", 7200))
    if now - last_two_hour_post < interval:
        return
    if should_pause_game_event(chat_id):
        log.info(
            "Двухчасовое сообщение отложено: chat_id=%s, высокая нагрузка.",
            chat_id,
        )
        return

    queued = enqueue_noncritical(chat_id, choose_two_hour_message(chat_id))
    if queued:
        update_chat_timer(chat_id, "last_two_hour_post", now)
        log.info("Отправлено двухчасовое сообщение: chat_id=%s.", chat_id)
    else:
        log.info(
            "Двухчасовое сообщение не помещено в очередь: chat_id=%s. "
            "Повтор будет через минуту.",
            chat_id,
        )


def send_links_message(chat_id, now, last_activity, last_links_post):
    if not links_enabled:
        return
    if is_chat_silenced(chat_id):
        return
    interval = int(cfg.get("links_interval_seconds", 43200))
    if now - last_links_post < interval:
        return

    min_pause = int(cfg.get("links_quiet_pause_min_seconds", 120))
    max_pause = int(cfg.get("links_quiet_pause_max_seconds", 300))
    # Стабильная пауза для текущего периода, чтобы она не менялась каждую минуту.
    quiet_delay = min_pause + abs(hash(f"{chat_id}:{int(last_links_post)}")) % max(
        1, max_pause - min_pause + 1
    )
    if now - last_activity < quiet_delay:
        return

    reserve_text = cfg.get(
        "reserve_links_text",
        "🔗 Запасные ссылки игры:\n\n"
        "🌐 wekings.online\n"
        "🌐 playwekings.mobi\n"
        "🌐 playwekings.ru\n"
        "🌐 proxy.playwekings.ru\n"
        "🌐 wekings.mobi",
    )

    bot.send_message(chat_id, reserve_text)
    update_chat_timer(chat_id, "last_links_post", now)
    log.info("Отправлены ссылки игры: chat_id=%s.", chat_id)


def timer_scheduler_worker():
    """
    Надёжный планировщик:
    - раз в минуту читает группы из SQLite;
    - каждый таймер работает независимо;
    - ошибка одного задания не останавливает остальные;
    - время обновляется только после успешной отправки.
    """
    heartbeat_every = int(cfg.get("timer_heartbeat_log_seconds", 600))
    last_heartbeat = 0.0

    while True:
        cycle_started = time.time()

        try:
            timers = get_all_chat_timers()
        except Exception:
            log.exception("Не удалось прочитать таблицу таймеров.")
            time.sleep(60)
            continue

        if cycle_started - last_heartbeat >= heartbeat_every:
            log.info(
                "Планировщик работает. Известных групп: %s.",
                len(timers),
            )
            last_heartbeat = cycle_started

        for (
            chat_id,
            last_activity,
            last_idle_post_value,
            last_two_hour_post,
            last_links_post,
        ) in timers:
            now = time.time()

            try:
                send_idle_message(
                    chat_id,
                    now,
                    float(last_activity),
                    float(last_idle_post_value),
                )
            except Exception:
                log.exception(
                    "Ошибка таймера 30 минут: chat_id=%s.", chat_id
                )

            try:
                send_two_hour_message(
                    chat_id,
                    now,
                    float(last_two_hour_post),
                )
            except Exception:
                log.exception(
                    "Ошибка двухчасового таймера: chat_id=%s.", chat_id
                )

            try:
                send_links_message(
                    chat_id,
                    now,
                    float(last_activity),
                    float(last_links_post),
                )
            except Exception:
                log.exception(
                    "Ошибка таймера ссылок: chat_id=%s.", chat_id
                )

        elapsed = time.time() - cycle_started
        time.sleep(max(5, 60 - elapsed))



def memory_cleanup_worker():
    """Периодически очищает устаревшие записи, не трогая настройки и капчу."""
    while True:
        time.sleep(3600)
        now = time.time()

        with STATE_LOCK:
            for key, value in list(ADMIN_CACHE.items()):
                if now - value[1] > ADMIN_CACHE_TTL * 2:
                    ADMIN_CACHE.pop(key, None)

            for key, value in list(LAST_USER_REPLY.items()):
                if now - value > 3600:
                    LAST_USER_REPLY.pop(key, None)

            for key, value in list(LAST_CHAT_REPLY.items()):
                if now - value > 3600:
                    LAST_CHAT_REPLY.pop(key, None)

            for key, dq in list(mention_history.items()):
                recent = [stamp for stamp in dq if now - stamp <= 900]
                if recent:
                    mention_history[key] = deque(recent, maxlen=30)
                else:
                    mention_history.pop(key, None)

            for key, dq in list(message_times.items()):
                recent = [stamp for stamp in dq if now - stamp <= 60]
                if recent:
                    message_times[key] = deque(recent, maxlen=12)
                else:
                    message_times.pop(key, None)

            for key in list(warning_count.keys()):
                if key not in message_times:
                    # Старые предупреждения не держим бесконечно в памяти.
                    warning_count.pop(key, None)

            for key, dq in list(bad_word_violations.items()):
                recent = [stamp for stamp in dq if now - stamp <= 900]
                if recent:
                    bad_word_violations[key] = deque(recent, maxlen=3)
                else:
                    bad_word_violations.pop(key, None)

            for chat_id, dq in list(chat_load_windows.items()):
                recent = [stamp for stamp in dq if now - stamp <= 600]
                if recent:
                    chat_load_windows[chat_id] = deque(recent, maxlen=600)
                else:
                    chat_load_windows.pop(chat_id, None)
                    chat_high_load_until.pop(chat_id, None)

        log.info("Очистка временной памяти завершена.")


def captcha_cleanup_worker():
    while True:
        # Проверяем чаще, чтобы капча удалялась почти сразу после истечения времени.
        time.sleep(10)
        timeout = int(cfg.get("captcha_timeout_seconds", 120))
        now = time.time()

        for key, data in list(captcha_users.items()):
            if now - data["created"] <= timeout:
                continue

            chat_id, user_id = key
            captcha_message_id = data.get("message_id")

            # Вопрос капчи удаляем независимо от того, удалось ли удалить участника.
            if captcha_message_id:
                safe_delete(chat_id, captcha_message_id)

            try:
                bot.ban_chat_member(chat_id, user_id)
                bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                send_temp(
                    chat_id,
                    "⏳ Новый участник не прошёл проверку и был удалён.",
                    seconds=20,
                )
            except Exception as e:
                log.warning(
                    "Не удалось удалить участника после капчи "
                    "chat_id=%s user_id=%s: %s",
                    chat_id,
                    user_id,
                    e,
                )
            finally:
                # Повторяем удаление на случай временной ошибки Telegram.
                if captcha_message_id:
                    safe_delete(chat_id, captcha_message_id)
                with STATE_LOCK:
                    captcha_users.pop(key, None)

def self_diagnostics_worker():
    """Раз в сутки проверяет основные компоненты и пишет результат только в лог."""
    while True:
        time.sleep(86400)
        try:
            me = bot.get_me()
            db_execute("SELECT 1", fetchone=True)
            log.info(
                "Самодиагностика OK: bot=@%s, queue=%s/%s, captcha=%s, chats=%s",
                me.username,
                NONCRITICAL_QUEUE.qsize(),
                NONCRITICAL_QUEUE.maxsize,
                len(captcha_users),
                len(get_all_chat_timers()),
            )
        except Exception:
            log.exception("Самодиагностика обнаружила ошибку.")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Police Bot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

if __name__ == "__main__":
    init_database()

    try:
        bot.remove_webhook()
    except Exception:
        pass

    BOT_ME = bot.get_me()
    BOT_ID = BOT_ME.id
    BOT_USERNAME = (BOT_ME.username or "").lower()

    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=noncritical_sender_worker, daemon=True).start()
    threading.Thread(target=timer_scheduler_worker, daemon=True).start()
    threading.Thread(target=captcha_cleanup_worker, daemon=True).start()
    threading.Thread(target=goblin_scheduler_worker, daemon=True).start()
    threading.Thread(target=secret_gift_worker, daemon=True).start()
    threading.Thread(target=memory_cleanup_worker, daemon=True).start()
    threading.Thread(target=self_diagnostics_worker, daemon=True).start()

    log.info("Police Bot запущен как @%s (%s)", BOT_ME.username, BOT_ME.id)

    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=20,
                long_polling_timeout=20,
                allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
            )
        except Exception:
            log.exception("Polling остановился. Повторный запуск через 5 секунд.")
            time.sleep(5)
