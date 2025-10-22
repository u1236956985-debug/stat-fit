from __future__ import annotations

import os
import json
import time
import tempfile
import threading
from typing import Dict, Any, Set, List, Optional
from concurrent.futures import ThreadPoolExecutor

from telebot import TeleBot, types
import config as cfg  # исправленный импорт модуля целиком

# Экспортируемые объекты и синхронизация
bot = TeleBot(cfg.BOT_TOKEN)
STATE_FILE = os.getenv("STATE_FILE", "state.json")
STATE_LOCK = threading.RLock()
SHUTDOWN_EVENT = threading.Event()
EXECUTOR = ThreadPoolExecutor(max_workers=5, thread_name_prefix="bot-util")

# Константы
ALLOWED_FILE_EXTS: tuple[str, ...] = (".pdf", ".doc", ".docx")
STATUS_NEW = "Новая"
STATUS_REVIEWING = "На проверке"
STATUS_CHECKED = "Проверено"
STATUS_REJECTED = "Отклонено"
POSSIBLE_CHAT_FIELDS = ["telegram_chat_id", "tg_chat_id", "chat_id", "telegram_id", "tg_id"]

# Состояние (с подкачкой из файла)
SENT_COURSEWORK_IDS: Set[str] = set()
TEACHER_CACHE_BY_CHAT: Dict[str, Dict[str, Any]] = {}
ADMIN_USERS: Set[int] = set()

def _load_state() -> None:
    global SENT_COURSEWORK_IDS, TEACHER_CACHE_BY_CHAT, ADMIN_USERS
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with STATE_LOCK:
            SENT_COURSEWORK_IDS = set(map(str, data.get("sent_coursework_ids", [])))
            TEACHER_CACHE_BY_CHAT = {
                str(k): v for k, v in (data.get("teacher_cache_by_chat") or {}).items() if isinstance(v, dict)
            }
            ADMIN_USERS = set(int(x) for x in data.get("admin_users", []))
    except Exception as e:
        print(f"state load error: {e}")

def save_state() -> None:
    try:
        with STATE_LOCK:
            data = {
                "sent_coursework_ids": list(SENT_COURSEWORK_IDS),
                "teacher_cache_by_chat": TEACHER_CACHE_BY_CHAT,
                "admin_users": list(ADMIN_USERS),
            }
        fd, tmp_path = tempfile.mkstemp(prefix="state.", suffix=".json", dir=os.path.dirname(STATE_FILE) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        print(f"state save error: {e}")

_load_state()

# Антифлуд
class RateLimiter:
    def __init__(self, limits: dict):
        self.limits = limits
        self.state: Dict[tuple[int, str], Dict[str, Any]] = {}
        self.dups: Dict[int, Dict[str, Any]] = {}
        self.lock = threading.RLock()

    def _now(self) -> float:
        return time.time()

    def allow(self, uid: int, kind: str) -> tuple[bool, int]:
        with self.lock:
            now = self._now()
            key = (int(uid), kind)
            st = self.state.get(key)
            if not st:
                st = {'ts': [], 'blocked_until': 0.0}
                self.state[key] = st

            if now < st['blocked_until']:
                return False, int(st['blocked_until'] - now) + 1

            long_n, long_win = self.limits[kind]['long']
            st['ts'] = [t for t in st['ts'] if now - t <= long_win]

            short_n, short_win = self.limits[kind]['short']
            cnt_short = sum(1 for t in st['ts'] if now - t <= short_win)
            cnt_long = len(st['ts'])

            if cnt_short >= short_n or cnt_long >= long_n:
                st['blocked_until'] = now + self.limits[kind]['cooldown']
                return False, int(st['blocked_until'] - now) + 1

            st['ts'].append(now)
            return True, 0

    def is_duplicate(self, uid: int, text: Optional[str], window_sec: float = 2.0) -> bool:
        if not text:
            return False
        with self.lock:
            now = self._now()
            h = hash(text)
            prev = self.dups.get(uid)
            if prev and prev.get('last_hash') == h and (now - prev.get('last_at', 0)) < window_sec:
                return True
            self.dups[uid] = {'last_hash': h, 'last_at': now}
            return False

LIMITS = {
    'msg': {'short': (5, 10), 'long': (20, 60), 'cooldown': 30},
    'cb':  {'short': (10, 10), 'long': (60, 60), 'cooldown': 20},
}
RATE_LIMITER = RateLimiter(LIMITS)

def anti_flood(kind: str = 'msg'):
    def deco(func):
        def wrapper(obj, *args, **kwargs):
            try:
                uid = getattr(getattr(obj, "from_user", None), "id", None)
                chat = getattr(obj, "chat", None) or getattr(getattr(obj, "message", None), "chat", None)
                chat_id = getattr(chat, "id", None)
            except Exception:
                uid, chat_id = None, None

            if kind == 'msg':
                text = getattr(obj, 'text', None)
                if uid is not None and RATE_LIMITER.is_duplicate(uid, text):
                    try:
                        m = bot.send_message(chat_id, "⚠️ Повтор того же сообщения. Подождите немного.")
                        auto_delete_message(chat_id, m.message_id, delay=3)
                    except Exception:
                        pass
                    return

            if uid is not None:
                allowed, retry = RATE_LIMITER.allow(uid, kind)
                if not allowed:
                    try:
                        if hasattr(obj, 'id'):
                            bot.answer_callback_query(obj.id, f"⏳ Слишком часто. Подождите {retry} с.")
                        if chat_id is not None:
                            m = bot.send_message(chat_id, f"⏳ Слишком часто. Повторите через {retry} с.")
                            auto_delete_message(chat_id, m.message_id, delay=min(6, retry + 1))
                    except Exception:
                        pass
                    return

            return func(obj, *args, **kwargs)
        return wrapper
    return deco

# Утилиты и клавиатуры
def _norm_ext(url_or_name: str) -> str:
    s = (url_or_name or "").lower().split("?")[0]
    for ext in ALLOWED_FILE_EXTS:
        if s.endswith(ext):
            return ext
    return ""

def extract_file_urls(cw: Dict[str, Any]) -> List[Dict[str, str]]:
    import os as _os
    urls: List[Dict[str, str]] = []

    def add_url(u, name=None):
        if not u:
            return
        ext = _norm_ext(u)
        if not ext:
            return
        base_name = name or _os.path.basename(u.split("?")[0])
        if not _norm_ext(base_name):
            base_name += ext
        urls.append({"url": u, "name": base_name})

    fields = [("files", True), ("attachments", True), ("documents", True), ("file_urls", True), ("file_url", False)]
    for key, is_list in fields:
        if key in cw and cw[key]:
            val = cw[key]
            if is_list and isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        add_url(item)
                    elif isinstance(item, dict):
                        add_url(item.get("url") or item.get("href"), item.get("name") or item.get("filename"))
            elif isinstance(val, str):
                add_url(val)

    uniq = {it["url"]: it for it in urls}
    return list(uniq.values())

def add_back_button(kb: types.InlineKeyboardMarkup, callback_data: str = "start"):
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data=callback_data))
    return kb

def back_kb(target: str = "start") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    add_back_button(kb, target)
    return kb

def start_menu() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🆔 Мой chat_id", callback_data="get_id"),
        types.InlineKeyboardButton("❓ Помощь", callback_data="help_main"),
        types.InlineKeyboardButton("👨🏫 Я преподаватель", callback_data="teacher_main"),
    )
    return kb

def admin_main_menu() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("⏳ В ожидании", callback_data="admin_pending"),
        types.InlineKeyboardButton("🔍 Поиск преподавателя", callback_data="admin_search"),
        types.InlineKeyboardButton("🔙 Главное меню", callback_data="start"),
    )
    return kb

def grade_menu_kb(cw_id: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=4)
    kb.add(*[types.InlineKeyboardButton(f"⭐ {g}", callback_data=f"set_grade_{cw_id}_{g}") for g in range(2, 6)])
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{cw_id}"))
    kb.add(types.InlineKeyboardButton("❓ Помощь", callback_data="help_grading"))
    return kb

def coursework_card_kb(cw_id: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📋 На проверке", callback_data=f"status_reviewing_{cw_id}"),
        types.InlineKeyboardButton("✅ Проверено – выбрать оценку", callback_data=f"grade_menu_{cw_id}"),
    )
    kb.add(types.InlineKeyboardButton("✍️ Ручная проверка", callback_data=f"t_manual_{cw_id}"))
    add_back_button(kb)
    return kb

def get_contextual_help(context: str) -> str:
    help_texts = {
        "main": "ℹ️ Это главное меню, используйте кнопки навигации или кодовое слово для админ‑доступа.",
        "admin": "ℹ️ Админ‑панель для мониторинга курсовых, выберите нужный раздел.",
        "teachers": "ℹ️ Список преподавателей и просмотр их курсовых.",
        "grading": "ℹ️ Выбор оценки 2–5, после выбора статус станет 'Проверено'.",
    }
    return help_texts.get(context, "ℹ️ Справка недоступна для данного раздела.")

def teacher_chat_id_from_teacher(teacher: Dict[str, Any] | None) -> Optional[int]:
    if not teacher:
        return None
    for fld in POSSIBLE_CHAT_FIELDS:
        val = teacher.get(fld)
        if val:
            try:
                return int(val)
            except Exception:
                pass
    return None

# Сервис: автоудаление сообщений
def _delete_task(chat_id: int, message_id: int, delay: int):
    time.sleep(delay)
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def auto_delete_message(chat_id: int, message_id: int, delay: int = 3) -> None:
    try:
        EXECUTOR.submit(_delete_task, chat_id, message_id, delay)
    except Exception:
        pass
