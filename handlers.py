import time
import threading
from io import BytesIO
from typing import Optional, Dict, Any, List
import requests
from telebot import types
# импорты вверху файла:
from core import bot, auto_delete_message, back_kb, start_menu, admin_main_menu, grade_menu_kb, coursework_card_kb


from core import (
    bot,
    # клавиатуры и утилиты из core
    start_menu, back_kb, admin_main_menu, grade_menu_kb, coursework_card_kb,
    get_contextual_help, auto_delete_message, extract_file_urls,
    # константы и состояние
    STATUS_NEW, STATUS_REVIEWING, STATUS_CHECKED, STATUS_REJECTED, POSSIBLE_CHAT_FIELDS,
    SENT_COURSEWORK_IDS, TEACHER_CACHE_BY_CHAT, ADMIN_USERS,
    # синхронизация и сервисы
    STATE_LOCK, SHUTDOWN_EVENT, save_state, anti_flood,
    # вспомогательное
    teacher_chat_id_from_teacher,
)
from api import (
    get_teachers, get_teacher, get_student,
    get_courseworks, get_coursework, update_coursework,
)
from config import ADMIN_PASSWORD

# =========================
# Вспомогательные функции
# =========================

def is_admin(uid: int) -> bool:
    with STATE_LOCK:
        return uid in ADMIN_USERS

def teacher_from_chat(chat_id: int) -> Optional[Dict[str, Any]]:
    key = str(chat_id)
    with STATE_LOCK:
        if key in TEACHER_CACHE_BY_CHAT:
            return TEACHER_CACHE_BY_CHAT[key]
    teachers = get_teachers()
    for t in teachers:
        for fld in POSSIBLE_CHAT_FIELDS:
            val = t.get(fld)
            if val is not None and str(val) == str(chat_id):
                with STATE_LOCK:
                    TEACHER_CACHE_BY_CHAT[key] = t
                    save_state()
                return t
    return None

# Безопасная загрузка документов с ограничением размера
MAX_FILE_BYTES = 20 * 1024 * 1024
DL_TIMEOUT = (10, 30)  # connect, read
DL_RETRIES = 2

def _download_small_file(url: str, max_bytes: int = MAX_FILE_BYTES) -> Optional[BytesIO]:
    # HEAD для оценки Content-Length
    try:
        h = requests.head(url, timeout=10, allow_redirects=True)
        clen = int(h.headers.get("Content-Length") or 0)
        if clen and clen > max_bytes:
            print(f"skip large file: {clen} > {max_bytes} at {url}")
            return None
    except Exception:
        pass  # если HEAD не сработал, попробуем GET ниже

    for attempt in range(DL_RETRIES + 1):
        try:
            with requests.get(url, timeout=DL_TIMEOUT, stream=True) as r:
                r.raise_for_status()
                bio = BytesIO()
                total = 0
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    bio.write(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        print(f"download exceeded limit {total} > {max_bytes} at {url}")
                        return None
                bio.seek(0)
                return bio
        except Exception as e:
            if attempt >= DL_RETRIES:
                print(f"download failed: {e} url={url}")
                return None
            time.sleep(0.7 * (attempt + 1))
    return None

# =========================
# Команды
# =========================

@bot.message_handler(commands=["start"])
@anti_flood('msg')
def cmd_start(msg):
    text = (
        "👋 Добро пожаловать в StartFit Bot!\n\n"
        "🤖 Помощь в работе с курсовыми.\n"
        "📱 Используйте кнопку ниже для получения ID.\n\n"
        "👨💼 Для админ-доступа отправьте кодовое слово."
    )
    bot.send_message(msg.chat.id, text, reply_markup=start_menu())

@bot.message_handler(commands=["help"])
@anti_flood('msg')
def cmd_help(msg):
    help_text = (
        "📖 Доступные команды:\n\n"
        "🔸 /start - Главное меню\n"
        "🔸 /help - Эта справка\n"
        "🔸 /admin - Админ-панель (после авторизации)\n\n"
        "🔐 Для админ-доступа отправьте кодовое слово."
    )
    bot.send_message(msg.chat.id, help_text, reply_markup=back_kb())

@bot.message_handler(commands=["admin"])
@anti_flood('msg')
def cmd_admin(msg):
    if not is_admin(msg.from_user.id):
        bot.send_message(
            msg.chat.id,
            "❌ Доступ запрещён!\n\nОтправьте кодовое слово для получения админ-доступа.",
            reply_markup=back_kb(),
        )
        return
    bot.send_message(msg.chat.id, "👨💼 Админ-панель:\n\nВыберите действие:", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: ADMIN_PASSWORD and m.text == ADMIN_PASSWORD)
@anti_flood('msg')
def admin_auth(msg):
    with STATE_LOCK:
        ADMIN_USERS.add(msg.from_user.id)
        save_state()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("👨💼 Открыть админ-панель", callback_data="admin_main"),
        types.InlineKeyboardButton("🔙 Главное меню", callback_data="start"),
    )
    try:
        bot.delete_message(msg.chat.id, msg.message_id)
    except Exception:
        pass
    bot.send_message(msg.chat.id, "🔓 Админ-доступ получен!\n👨💼 Добро пожаловать в панель управления:", reply_markup=kb)

# =========================
# Общие callback'и
# =========================

@bot.callback_query_handler(func=lambda c: c.data == "start")
@anti_flood('cb')
def on_start_cb(call):
    text = (
        "👋 Добро пожаловать в StartFit Bot!\n\n"
        "🤖 Помощь в работе с курсовыми.\n"
        "📱 Используйте кнопку ниже для получения ID.\n\n"
        "👨💼 Для админ-доступа отправьте кодовое слово."
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=start_menu())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "get_id")
@anti_flood('cb')
def on_get_id(call):
    text = (
        "🆔 Ваш Telegram chat_id:\n\n"
        f"{call.from_user.id}\n\n"
        "📋 Передайте этот ID администратору для добавления в систему.\n"
        "💡 Чтобы скопировать ID, нажмите на него."
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=back_kb())
    bot.answer_callback_query(call.id, "ID готов к копированию!")

@bot.callback_query_handler(func=lambda c: c.data.startswith("help_"))
@anti_flood('cb')
def on_help_cb(call):
    ctx = call.data.split("_", 1)[1]
    bot.answer_callback_query(call.id, get_contextual_help(ctx), show_alert=True)

# =========================
# Админ-панель
# =========================

@bot.callback_query_handler(func=lambda c: c.data == "admin_main")
@anti_flood('cb')
def on_admin_main(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Доступ запрещён")
        return
    bot.edit_message_text("👨💼 Админ-панель:\n\nВыберите действие:", call.message.chat.id, call.message.message_id, reply_markup=admin_main_menu())
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "admin_pending")
@anti_flood('cb')
def on_admin_pending(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Доступ запрещён")
        return
    teachers = get_teachers()
    courseworks = get_courseworks()
    if courseworks is None:
        bot.edit_message_text("❌ Не удалось загрузить данные", call.message.chat.id, call.message.message_id, reply_markup=back_kb("admin_main"))
        bot.answer_callback_query(call.id, "Ошибка загрузки данных")
        return
    pending_count: Dict[str, int] = {}
    for cw in courseworks:
        if cw.get("status") == STATUS_REVIEWING:
            tid = str(cw.get("teacher_id", ""))
            pending_count[tid] = pending_count.get(tid, 0) + 1
    text = "⏳ Курсовые в ожидании проверки:\n\n"
    for t in teachers:
        tid = str(t.get("id", ""))
        name = t.get("name", f"ID: {tid}")
        count = pending_count.get(tid, 0)
        text += f"👨🏫 {name}: {count}\n"
    total_pending = sum(pending_count.values())
    text += f"\n📊 Всего на проверке: {total_pending}"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=back_kb("admin_main"))
    bot.answer_callback_query(call.id, f"Найдено {total_pending} курсовых на проверке")

@bot.callback_query_handler(func=lambda c: c.data == "admin_search")
@anti_flood('cb')
def on_admin_search(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Доступ запрещён")
        return
    bot.edit_message_text(
        "🔍 Поиск преподавателя:\n\nВведите имя или ID преподавателя обычным сообщением.\nНапример: Иванов или 123",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=back_kb("admin_main"),
    )
    bot.answer_callback_query(call.id, "Введите имя или ID преподавателя")

@bot.message_handler(func=lambda m: is_admin(m.from_user.id))
@anti_flood('msg')
def admin_free_search(msg):
    q = (msg.text or "").strip().lower()
    if not q:
        return
    teachers = get_teachers()
    found: List[Dict[str, Any]] = []
    for t in teachers:
        tid = str(t.get("id", ""))
        name = (t.get("name") or "").lower()
        if q == tid or q in name:
            found.append(t)
    if not found:
        bot.reply_to(msg, "Ничего не найдено")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in found[:50]:
        tid = t.get("id")
        name = t.get("name", f"ID: {tid}")
        kb.add(types.InlineKeyboardButton(f"👨🏫 {name}", callback_data=f"view_{tid}"))
    bot.reply_to(msg, f"Найдено преподавателей: {len(found)}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("view_"))
@anti_flood('cb')
def on_view_teacher(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Доступ запрещён")
        return
    tid = call.data.split("_", 1)[1]
    cws = [cw for cw in (get_courseworks() or []) if str(cw.get("teacher_id", "")) == tid]
    teacher = get_teacher(tid)
    name = teacher.get("name", f"ID: {tid}") if teacher else f"ID: {tid}"
    if not cws:
        text = f"👨🏫 {name}\n\n📋 Курсовых работ не найдено"
    else:
        text = f"👨🏫 {name}\n\n📊 Всего курсовых: {len(cws)}\n\n"
        for i, cw in enumerate(cws, 1):
            sid = cw.get("student_id")
            student = get_student(sid) if sid else None
            sname = student.get("name", f"ID:{sid}") if student else "Неизвестен"
            status = cw.get("status", "?")
            grade = cw.get("grade")
            line = f"{i}. {cw.get('title','')} — {sname} — {status}"
            if grade:
                line += f" (⭐{grade})"
            text += line + "\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=back_kb("admin_main"))
    bot.answer_callback_query(call.id, f"Показано курсовых: {len(cws)}")

# =========================
# Курсовые: статусы/оценки
# =========================

@bot.callback_query_handler(func=lambda c: c.data.startswith("status_reviewing_"))
@anti_flood('cb')
def on_status_reviewing(call):
    cid = call.data.split("_")[-1]
    if update_coursework(cid, STATUS_REVIEWING):
        bot.answer_callback_query(call.id, "✅ Статус изменен на 'На проверке'!")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        sent = bot.send_message(call.message.chat.id, "✅ Обновлено: На проверке")
        auto_delete_message(call.message.chat.id, sent.message_id, delay=3)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка обновления статуса")

@bot.callback_query_handler(func=lambda c: c.data.startswith("grade_menu_"))
@anti_flood('cb')
def on_grade_menu(call):
    cid = call.data.split("_")[-1]
    new_text = (call.message.text or "") + "\n\n📝 Выберите оценку:"
    bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=grade_menu_kb(cid))
    bot.answer_callback_query(call.id, "Выберите оценку 2–5")

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_grade_"))
@anti_flood('cb')
def on_set_grade(call):
    _, _, cid, gr = call.data.split("_")
    try:
        grade = int(gr)
    except Exception:
        bot.answer_callback_query(call.id, "❌ Некорректная оценка")
        return
    if update_coursework(cid, STATUS_CHECKED, grade=grade):
        bot.answer_callback_query(call.id, f"✅ Оценка {grade} сохранена, статус 'Проверено'!")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        sent = bot.send_message(call.message.chat.id, f"✅ Проверено! Оценка: {grade}")
        auto_delete_message(call.message.chat.id, sent.message_id, delay=5)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка сохранения оценки")

@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
@anti_flood('cb')
def on_cancel(call):
    cid = call.data.split("_")[-1]
    kb = coursework_card_kb(cid)
    orig = (call.message.text or "").split("\n\n📝")[0]
    bot.edit_message_text(orig, call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id, "❌ Выбор оценки отменён")

# =========================
# Преподаватель
# =========================

@bot.callback_query_handler(func=lambda c: c.data == "teacher_main")
@anti_flood('cb')
def on_teacher_main(call):
    teacher = teacher_from_chat(call.from_user.id)
    if not teacher:
        bot.answer_callback_query(call.id, "❌ Нет регистрации преподавателя")
        return
    bot.edit_message_text(
        f"👨🏫 Преподаватель: {teacher.get('name')}\n\nВыберите действие:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=_teacher_main_menu(),
    )
    bot.answer_callback_query(call.id)

def _teacher_main_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("👥 Мои ученики", callback_data="my_students"),
        types.InlineKeyboardButton("✍️ Ручная проверка", callback_data="manual_review_list"),
        types.InlineKeyboardButton("🔙 Главное меню", callback_data="start"),
    )
    return kb

@bot.callback_query_handler(func=lambda c: c.data == "my_students")
@anti_flood('cb')
def on_my_students(call):
    teacher = teacher_from_chat(call.from_user.id)
    students = teacher.get("students", []) if teacher else []
    if not students:
        text = "👥 Ваши ученики не найдены."
    else:
        text = "👥 Ваши ученики:\n\n" + "\n".join(f"• {s.get('name')} (ID: {s.get('id')})" for s in students)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=back_kb("teacher_main"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "manual_review_list")
@anti_flood('cb')
def on_manual_review_list(call):
    teacher = teacher_from_chat(call.from_user.id)
    if not teacher:
        bot.answer_callback_query(call.id, "❌ Нет регистрации преподавателя")
        return
    all_cw = get_courseworks() or []
    to_review = [cw for cw in all_cw if str(cw.get("teacher_id")) == str(teacher.get("id")) and cw.get("status") == STATUS_REVIEWING]
    if not to_review:
        text = "✍️ Курсовых для ручной проверки не найдено."
        kb = back_kb("teacher_main")
    else:
        text = "✍️ Курсовые для ручной проверки:\n\n" + "\n".join(f"• {cw.get('title')} (ID: {cw.get('id')})" for cw in to_review)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for cw in to_review:
            kb.add(types.InlineKeyboardButton(f"{cw.get('title')}", callback_data=f"t_manual_{cw.get('id')}"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="teacher_main"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("t_manual_"))
@anti_flood('cb')
def on_teacher_manual(call):
    cw_id = call.data.split("_")[-1]
    cw = get_coursework(cw_id)
    if not cw:
        bot.answer_callback_query(call.id, "❌ Курсовая не найдена")
        return
    student = get_student(cw.get("student_id"))
    text = (
        "✍️ Ручная проверка:\n\n"
        f"📋 Название: {cw.get('title')}\n"
        f"👤 Студент: {student.get('name') if student else 'Неизвестен'}\n"
        f"🆔 ID: {cw_id}\n\n"
        "Выберите действие:"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Принять (На проверке)", callback_data=f"status_reviewing_{cw_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"set_reject_{cw_id}"),
    )
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="manual_review_list"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_reject_"))
@anti_flood('cb')
def on_set_reject(call):
    cw_id = call.data.split("_")[-1]
    if update_coursework(cw_id, STATUS_REJECTED):
        bot.answer_callback_query(call.id, "✅ Курсовая отклонена")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при отклонении")

# =========================
# Фоновая рассылка курсовых
# =========================

_POLL_THREAD: Optional[threading.Thread] = None

def _send_coursework_to_chat(chat_id: int, cw: Dict[str, Any], student: Optional[Dict[str, Any]] = None):
    cw_id = cw.get("id")
    title = cw.get("title", "Без названия")
    status = cw.get("status", STATUS_NEW)
    sname = student.get("name") if student else "Неизвестен"
    msg = (
        "📚 Курсовая работа\n"
        f"📋 Название: {title}\n"
        f"👤 Студент: {sname}\n"
        f"🆔 ID: {cw_id}\n"
        f"📊 Статус: {status}"
    )
    try:
        bot.send_message(chat_id, msg, reply_markup=coursework_card_kb(cw_id))
    except Exception as e:
        print(f"send card error cw {cw_id} chat {chat_id}: {e}")
        return
    files = extract_file_urls(cw)
    for i, f in enumerate(files, 1):
        url = f["url"]; name = f["name"]
        bio = _download_small_file(url)
        if not bio:
            continue
        bio.name = name
        try:
            bot.send_document(chat_id, bio, caption=f"📎 Файл {i}/{len(files)}")
        except Exception as e:
            print(f"file send error {url} cw {cw_id} chat {chat_id}: {e}")

def _poll_loop():
    err = 0
    base_sleep = 20
    while not SHUTDOWN_EVENT.is_set():
        try:
            cws = get_courseworks()
            with STATE_LOCK:
                sent = set(SENT_COURSEWORK_IDS)
            for cw in cws:
                cw_id = str(cw.get("id") or "")
                if not cw_id or cw_id in sent:
                    continue
                status = cw.get("status", "")
                if status not in (STATUS_NEW, STATUS_REVIEWING):
                    with STATE_LOCK:
                        SENT_COURSEWORK_IDS.add(cw_id)
                        save_state()
                    continue
                teacher_id = cw.get("teacher_id")
                teacher = get_teacher(teacher_id) if teacher_id else None
                chat_id = teacher_chat_id_from_teacher(teacher)
                student = get_student(cw.get("student_id")) if cw.get("student_id") else None
                if chat_id:
                    _send_coursework_to_chat(chat_id, cw, student=student)
                    with STATE_LOCK:
                        SENT_COURSEWORK_IDS.add(cw_id)
                        save_state()
            err = 0  # успешная итерация
        except Exception as e:
            err += 1
            print(f"[poll] error (#{err}): {e}")
        # экспоненциальный бэкофф
        sleep_s = base_sleep if err == 0 else min(base_sleep * (2 ** min(err, 5)), 300)
        for _ in range(int(sleep_s * 10)):
            if SHUTDOWN_EVENT.is_set():
                break
            time.sleep(0.1)

def start_background_poll():
    global _POLL_THREAD
    if _POLL_THREAD and _POLL_THREAD.is_alive():
        return
    _POLL_THREAD = threading.Thread(target=_poll_loop, daemon=True, name="poll-courseworks")
    _POLL_THREAD.start()

def stop_background_poll():
    SHUTDOWN_EVENT.set()
    t = _POLL_THREAD
    if t and t.is_alive():
        t.join(timeout=5)
