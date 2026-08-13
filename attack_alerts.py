"""Предупреждения о Драконе и Морском Змее напрямую по данным Монаха WEKINGS."""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot
import stats

logger = logging.getLogger("police.attack_alerts")
BASE_URL = "https://wekings.online/"
START_URL = urljoin(BASE_URL, "start")
LOCAL_TZ = ZoneInfo("Europe/Chisinau")
CHECK_INTERVAL_SECONDS = 30
THRESHOLDS = (60, 30, 5)
THRESHOLD_BANDS = {60: (30, 60), 30: (5, 30), 5: (0, 5)}
FETCH_TIMES = ((0, 2), (0, 7), (0, 12), (0, 22))
STATE_FILE = Path(__file__).resolve().parent / "attack_alerts_state.json"
SCHEDULE_FILE = Path(__file__).resolve().parent / "attack_schedule.json"

FUNNY_ALERTS = {
    "Дракона": {
        60: ("🐉 Дракон уже прогревает крылья! До его прихода 1 час. Точите мечи! ⚔️", "🐉 Через час прилетит Дракон. Пора собираться! 🔥"),
        30: ("🐉 Полчаса до Дракона! Допивайте чай и в строй. ⚔️", "🐉 Дракон через 30 минут. Проверяем мечи и щиты! 🔥"),
        5: ("🐉 Пять минут до Дракона! Всё, поздно притворяться спящими — в бой! ⚔️", "🐉 Дракон будет через 5 минут. Кто не спрятался — тот герой! 🔥", "🐉 До Дракона 5 минут! Берём меч, щит и боевое настроение!"),
    },
    "Морского Змея": {
        60: ("🐍 Морской Змей выплыл по наши души! До встречи 1 час. Готовьте оружие! ⚔️", "🐍 Через час приплывёт Морской Змей. Пора готовиться! 🌊"),
        30: ("🐍 Полчаса до Морского Змея! Готовимся к встрече. ⚔️", "🐍 Змей через 30 минут. Вода уже подозрительно шевелится! 🌊"),
        5: ("🐍 Пять минут до Морского Змея! Купальный сезон закрыт — в бой! ⚔️", "🐍 Змей будет через 5 минут. Он уже у берега! 🌊", "🐍 До Змея 5 минут! Хватайте оружие, удочки сегодня не помогут! 😄"),
    },
}
_last_message: dict[tuple[str, int], str] = {}

class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.forms=[]; self._form=None; self._anchor=None
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == "a": self._anchor={"href":a.get("href", ""), "text":""}
        elif tag == "form": self._form={"action":a.get("action", ""), "method":a.get("method", "get").lower(), "inputs":{}, "text":""}
        elif tag == "input" and self._form is not None:
            n=a.get("name"); t=a.get("type", "text").lower()
            if n and t in {"hidden", "submit"}: self._form["inputs"][n]=a.get("value", "")
        elif tag == "button" and self._form is not None:
            n=a.get("name")
            if n: self._form["inputs"][n]=a.get("value", "")
    def handle_data(self, data):
        if self._anchor is not None: self._anchor["text"] += data
        if self._form is not None: self._form["text"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None: self.links.append(self._anchor); self._anchor=None
        elif tag == "form" and self._form is not None: self.forms.append(self._form); self._form=None

def _parse_page(text):
    p=_PageParser(); p.feed(text); return p

def _plain(text):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()

def _load_json(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError): return default

def _save_json(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError: logger.exception("Не удалось сохранить %s", path.name)

def _load_sent(): return {str(x) for x in _load_json(STATE_FILE, {}).get("sent", [])}
def _save_sent(sent): _save_json(STATE_FILE, {"sent": sorted(sent)[-100:]})

def _parse_time(value):
    if not value: return None
    try:
        d=datetime.fromisoformat(str(value).replace("Z", "+00:00")); return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError: return None

def _message(event, threshold):
    variants=FUNNY_ALERTS[event][threshold]; prev=_last_message.get((event, threshold)); choices=[x for x in variants if x != prev] or list(variants)
    msg=random.choice(choices); _last_message[(event, threshold)]=msg
    return ("🔴🐉 " if event == "Дракона" else "🟢🐍 ") + msg.lstrip("🐉🐍 ")

async def _request_text(session, method, url, **kwargs):
    timeout=aiohttp.ClientTimeout(total=25)
    async with session.request(method, url, timeout=timeout, allow_redirects=True, **kwargs) as r:
        r.raise_for_status(); return await r.text(), str(r.url)

async def _follow_named_link(session, html, current_url, names):
    p=_parse_page(html)
    for link in p.links:
        txt=_plain(link["text"]).lower()
        if any(n.lower() in txt for n in names) and link["href"]:
            return await _request_text(session, "GET", urljoin(current_url, link["href"]))
    raise RuntimeError(f"Не найдена ссылка: {', '.join(names)}")

async def _open_guest_monk(session):
    html, url = await _request_text(session, "GET", START_URL)
    p=_parse_page(html)
    form=next((f for f in p.forms if "начать игру" in _plain(f["text"]).lower()), None) or (p.forms[0] if p.forms else None)
    if form:
        target=urljoin(url, form["action"] or url)
        if form["method"] == "post": html, url=await _request_text(session, "POST", target, data=form["inputs"])
        else: html, url=await _request_text(session, "GET", target, params=form["inputs"])
    else:
        # На некоторых версиях страницы «Начать игру» оформлено ссылкой.
        html, url = await _follow_named_link(session, html, url, ("Начать игру",))
    html, url = await _follow_named_link(session, html, url, ("Город",))
    html, url = await _follow_named_link(session, html, url, ("Монах",))
    return html

def _event_from_text(text, label, now_local):
    # Монах может показать либо обратный отсчёт «через H:MM[:SS]», либо время «в HH:MM».
    relative=re.search(rf"{label}.{{0,100}}?через\s*(\d{{1,2}})[:.]([0-5]\d)(?:[:.]([0-5]\d))?", text, re.I)
    if relative:
        hours, minutes=int(relative.group(1)), int(relative.group(2))
        seconds=int(relative.group(3) or 0)
        return (now_local + timedelta(hours=hours, minutes=minutes, seconds=seconds)).astimezone(timezone.utc)
    m=re.search(rf"{label}.{{0,140}}?(?:в\s*)?(\d{{1,2}})[:.]([0-5]\d)", text, re.I)
    if not m:
        m=re.search(rf"(\d{{1,2}})[:.]([0-5]\d).{{0,140}}?{label}", text, re.I)
    if not m: return None
    h, minute=int(m.group(1)), int(m.group(2))
    if h > 23: return None
    d=now_local.replace(hour=h, minute=minute, second=0, microsecond=0)
    if d < now_local - timedelta(minutes=2): d += timedelta(days=1)
    return d.astimezone(timezone.utc)

async def _fetch_from_monk():
    headers={"User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36", "Accept-Language":"ru-RU,ru;q=0.9"}
    jar=aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(headers=headers, cookie_jar=jar) as session:
        html=await _open_guest_monk(session)
    text=_plain(html); now=datetime.now(LOCAL_TZ)
    dragon=_event_from_text(text, r"Дракон", now)
    serpent=_event_from_text(text, r"Морск(?:ой|ого)\s+Зме[йя]", now)
    if not dragon or not serpent:
        raise RuntimeError("Монах не показал будущее время Дракона и Морского Змея")
    return {"date":now.date().isoformat(), "fetched_at":now.isoformat(), "dragon_at":dragon.isoformat(), "serpent_at":serpent.isoformat()}

async def _refresh_schedule():
    data=await _fetch_from_monk(); _save_json(SCHEDULE_FILE, data)
    logger.info("MONK SCHEDULE SAVED dragon=%s serpent=%s", data["dragon_at"], data["serpent_at"]); return data

def _today_schedule():
    data=_load_json(SCHEDULE_FILE, {})
    return data if data.get("date") == datetime.now(LOCAL_TZ).date().isoformat() else {}

async def schedule_status_text():
    data=_today_schedule()
    if not data:
        return "⚠️ Расписание на сегодня ещё не получено у Монаха. Автопроверка: 00:02, 00:07, 00:12 и 00:22."
    now=datetime.now(timezone.utc); lines=["✅ Уведомления о нападениях включены. Данные получены напрямую у Монаха WEKINGS."]
    for name, value in (("🔴🐉 Дракон", data.get("dragon_at")), ("🟢🐍 Морской Змей", data.get("serpent_at"))):
        at=_parse_time(value)
        if not at: lines.append(f"{name}: нет времени"); continue
        mins=int((at-now).total_seconds()//60)
        if mins < 0: lines.append(f"{name}: событие уже началось")
        else:
            h,m=divmod(mins,60); lines.append(f"{name}: через {h} ч. {m} мин." if h else f"{name}: через {m} мин.")
    return "\n".join(lines)

async def attack_alert_worker(bot: Bot):
    sent=_load_sent(); attempted=set()
    while True:
        try:
            local_now=datetime.now(LOCAL_TZ); today=local_now.date().isoformat(); data=_today_schedule()
            # Ровно одна успешная загрузка в день. Если не получилось — страховочные попытки.
            if not data:
                for h,m in FETCH_TIMES:
                    key=f"{today}|{h:02d}:{m:02d}"
                    target=local_now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if local_now >= target and key not in attempted:
                        attempted.add(key)
                        try:
                            data=await _refresh_schedule()
                        except Exception:
                            logger.exception("MONK FETCH FAILED attempt=%s", key)
                        break
            # Чистим отметки попыток прошлых дней.
            attempted={x for x in attempted if x.startswith(today+"|")}
            if data:
                now=datetime.now(timezone.utc); changed=False; chat_ids=stats.chat_ids()
                for event, value in (("Дракона", data.get("dragon_at")), ("Морского Змея", data.get("serpent_at"))):
                    at=_parse_time(value)
                    if not at: continue
                    left=(at-now).total_seconds()/60
                    if left <= 0: continue
                    for threshold in THRESHOLDS:
                        lower,upper=THRESHOLD_BANDS[threshold]
                        if lower < left <= upper:
                            key=f"{event}|{at.isoformat()}|{threshold}"
                            if key in sent: continue
                            delivered=False
                            for chat_id in chat_ids:
                                try: await bot.send_message(chat_id, _message(event, threshold)); delivered=True
                                except Exception: logger.exception("ATTACK ALERT SEND ERROR chat_id=%s", chat_id)
                            if delivered: sent.add(key); changed=True
                if changed: _save_sent(sent)
        except asyncio.CancelledError: raise
        except Exception: logger.exception("Ошибка worker Дракона/Змея")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
