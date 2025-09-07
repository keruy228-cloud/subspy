#!/usr/bin/env python3
"""
Полнофункціональний Telegram-бот для обробки замовлень (реєстрація/перев'язка),
керування групами менеджерів, чергою, завантаженням фото, погодженням етапів адміністртором
та збором заявок на співпрацю.

Примітки до запуску:
- Встановіть BOT_TOKEN у змінних середовища або замініть значення BOT_TOKEN нижче.
- При необхідності змініть ADMIN_GROUP_ID та ADMIN_ID або теж задайте через ENV.
- Файл instructions.py повинен експортувати словник INSTRUCTIONS у форматі:
  INSTRUCTIONS = {"BankName": {"register": [...], "change": [...]}, ...}
"""

import os
import sys
import sqlite3
import logging
from typing import Optional, Tuple, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

# Популярна структура інструкцій (повинен бути окремий файл instructions.py)
try:
    from instructions import INSTRUCTIONS
except Exception:
    INSTRUCTIONS = {}  # Якщо немає - бот все ще працюватиме, але банки будуть пусті.

# ====== Конфігурація ======
BOT_TOKEN = "8303921633:AAFu3nvim6qggmkIq2ghg5EMrT-8RhjoP50"
ADMIN_GROUP_ID = -4930176305
ADMIN_ID = 7797088374

LOCK_FILE = "bot.lock"
DB_FILE = os.getenv("DB_FILE", "orders.db")

# ====== Логування ======
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ====== Захист від подвійного запуску ======
if os.path.exists(LOCK_FILE):
    print("⚠️ bot.lock виявлено — ймовірно бот вже запущений. Завершую роботу.")
    sys.exit(1)
open(LOCK_FILE, "w").close()


# ====== Підключення до БД ======
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

# Створюємо таблиці
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    bank TEXT,
    action TEXT,
    stage INTEGER DEFAULT 0,
    status TEXT,
    group_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS order_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    stage INTEGER,
    file_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cooperation_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS manager_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER UNIQUE,
    name TEXT,
    busy INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    bank TEXT,
    action TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# ====== Динамічні списки банків ======
BANKS_REGISTER = [bank for bank, actions in INSTRUCTIONS.items() if "register" in actions and actions["register"]]
BANKS_CHANGE = [bank for bank, actions in INSTRUCTIONS.items() if "change" in actions and actions["change"]]

# ====== Runtime state (тимчасово, для зручності) ======
user_states: Dict[int, Dict[str, Any]] = {}  # user_id -> {"order_id", "bank", "action", "stage", "age_required"}

# ====== Conversation states ======
COOPERATION_INPUT = 0


# ====== Утиліти БД/логіки ======
def find_age_requirement(bank: str, action: str) -> Optional[int]:
    steps = INSTRUCTIONS.get(bank, {}).get(action, [])
    for step in steps:
        if isinstance(step, dict) and "age" in step:
            return step["age"]
    return None

def create_order_in_db(user_id: int, username: str, bank: str, action: str) -> int:
    cursor.execute(
        "INSERT INTO orders (user_id, username, bank, action, stage, status) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, bank, action, 0, "На етапі 1")
    )
    conn.commit()
    return cursor.lastrowid

def update_order_stage_db(order_id: int, new_stage: int, status: Optional[str] = None):
    if status is None:
        cursor.execute("UPDATE orders SET stage=? WHERE id=?", (new_stage, order_id))
    else:
        cursor.execute("UPDATE orders SET stage=?, status=? WHERE id=?", (new_stage, status, order_id))
    conn.commit()

def set_order_group_db(order_id: int, group_chat_id: int):
    cursor.execute("UPDATE orders SET group_id=? WHERE id=?", (group_chat_id, order_id))
    conn.commit()

def free_group_db_by_chatid(group_chat_id: int):
    cursor.execute("UPDATE manager_groups SET busy=0 WHERE group_id=?", (group_chat_id,))
    conn.commit()

def occupy_group_db_by_dbid(group_db_id: int):
    cursor.execute("UPDATE manager_groups SET busy=1 WHERE id=?", (group_db_id,))
    conn.commit()

def get_free_groups(limit: Optional[int] = None):
    q = "SELECT id, group_id FROM manager_groups WHERE busy=0 ORDER BY id ASC"
    if limit:
        q += f" LIMIT {limit}"
    cursor.execute(q)
    return cursor.fetchall()

def pop_queue_next():
    cursor.execute("SELECT id, user_id, username, bank, action FROM queue ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        return None
    qid, user_id, username, bank, action = row
    cursor.execute("DELETE FROM queue WHERE id=?", (qid,))
    conn.commit()
    return (user_id, username, bank, action)

def enqueue_user(user_id: int, username: str, bank: str, action: str):
    cursor.execute("INSERT INTO queue (user_id, username, bank, action) VALUES (?, ?, ?, ?)",
                   (user_id, username, bank, action))
    conn.commit()

def get_last_order_for_user(user_id: int) -> Optional[Tuple]:
    cursor.execute("SELECT id, bank, action, stage, status, group_id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    return cursor.fetchone()

def get_order_by_id(order_id: int) -> Optional[Tuple]:
    cursor.execute("SELECT id, user_id, username, bank, action, stage, status, group_id FROM orders WHERE id=?", (order_id,))
    return cursor.fetchone()


# ====== Призначення груп/черги (має бути визначено ДО викликів) ======
async def assign_group_or_queue(order_id: int, user_id: int, username: str, bank: str, action: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cursor.execute("SELECT id, group_id, name FROM manager_groups WHERE busy=0 ORDER BY id ASC LIMIT 1")
    free_group = cursor.fetchone()
    if free_group:
        group_db_id, group_chat_id, group_name = free_group
        try:
            occupy_group_db_by_dbid(group_db_id)
            set_order_group_db(order_id, group_chat_id)
            logger.info("Order %s assigned to group %s (%s)", order_id, group_chat_id, group_name)
            msg = f"📢 Нова заявка від @{username} (ID: {user_id})\n🏦 {bank} — {action}\nOrderID: {order_id}"
            try:
                await context.bot.send_message(chat_id=group_chat_id, text=msg)
            except Exception as e:
                logger.warning("Не вдалося відправити повідомлення в групу %s: %s", group_chat_id, e)
            return True
        except Exception as e:
            logger.exception("assign_group_or_queue error while assigning: %s", e)
            return False
    else:
        try:
            enqueue_user(user_id, username, bank, action)
            try:
                await context.bot.send_message(chat_id=user_id, text="⏳ Усі менеджери зайняті. Ви в черзі. Отримаєте повідомлення, коли звільниться місце.")
            except Exception:
                logger.warning("Не вдалося повідомити користувача в черзі (ID=%s)", user_id)
            logger.info("User %s (order %s) enqueued", user_id, order_id)
        except Exception as e:
            logger.exception("assign_group_or_queue error while enqueue: %s", e)
        return False

# ====== Призначення з черги для всіх вільних груп ======
async def assign_queued_clients_to_free_groups(context: ContextTypes.DEFAULT_TYPE):
    try:
        free_groups = get_free_groups()
        if not free_groups:
            return

        for group_db_id, group_chat_id in free_groups:
            next_client = pop_queue_next()
            if not next_client:
                break
            user_id, username, bank, action = next_client

            new_order_id = create_order_in_db(user_id, username, bank, action)

            try:
                occupy_group_db_by_dbid(group_db_id)
                set_order_group_db(new_order_id, group_chat_id)
            except Exception as e:
                logger.exception("Error occupying group or setting order group: %s", e)

            user_states[user_id] = {"order_id": new_order_id, "bank": bank, "action": action, "stage": 0, "age_required": find_age_requirement(bank, action)}
            try:
                await context.bot.send_message(chat_id=user_id, text="✅ Звільнилося місце! Починаємо реєстрацію.")
                await send_instruction(user_id, context)
            except Exception as e:
                logger.warning("Не вдалося повідомити користувача після призначення з черги: %s", e)
    except Exception as e:
        logger.exception("assign_queued_clients_to_free_groups error: %s", e)

# ====== Відправка інструкцій користувачу ======
async def send_instruction(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Відправляє інструкцію для поточного етапу користувачу.
    Якщо етапів більше немає — завершує замовлення, звільняє групу та сповіщає адмінів.
    """
    state = user_states.get(user_id)
    if not state:
        # спробуємо підвантажити останнє замовлення з БД
        row = get_last_order_for_user(user_id)
        if not row:
            logger.warning("send_instruction: не знайдено замовлення для користувача %s", user_id)
            try:
                await context.bot.send_message(chat_id=user_id, text="❌ Помилка: замовлення не знайдено. Почніть заново командою /start")
            except Exception:
                pass
            return
        order_id, bank, action, stage, status, group_id = row[0], row[1], row[2], row[3], row[4], row[5]
        user_states[user_id] = {"order_id": order_id, "bank": bank, "action": action, "stage": stage, "age_required": find_age_requirement(bank, action)}
        state = user_states[user_id]

    order_id = state.get("order_id")
    bank = state.get("bank")
    action = state.get("action")
    stage = state.get("stage", 0)

    steps = INSTRUCTIONS.get(bank, {}).get(action, [])
    if not steps:
        # Нема інструкцій — завершити замовлення з повідомленням адміну
        update_order_stage_db(order_id, stage, status="Помилка: інструкції відсутні")
        logger.warning("No instructions for %s %s (order %s)", bank, action, order_id)
        try:
            await context.bot.send_message(chat_id=user_id, text="❌ Помилка: інструкції для обраного банку/операції відсутні. Зв'яжіться з менеджером.")
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"⚠️ Для замовлення {order_id} немає інструкцій: {bank} {action}")
        except Exception:
            pass
        return

    # Коли stage >= len(steps) — завершено
    if stage >= len(steps):
        update_order_stage_db(order_id, stage, status="Завершено")
        # звільнити групу
        order = get_order_by_id(order_id)
        if order:
            group_chat_id = order[7]
            if group_chat_id:
                try:
                    free_group_db_by_chatid(group_chat_id)
                except Exception:
                    pass
        # повідомлення користувачу та адміну
        try:
            await context.bot.send_message(chat_id=user_id, text="✅ Ваше замовлення завершено. Дякуємо!")
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"✅ Замовлення {order_id} виконано для @{order[2]} (ID: {order[1]})")
        except Exception:
            pass
        # видалити сесію
        user_states.pop(user_id, None)
        # після звільнення групи — призначити з черги
        try:
            await assign_queued_clients_to_free_groups(context)
        except Exception:
            pass
        return

    # Отримати поточний крок
    step = steps[stage]
    if isinstance(step, dict):
        text = step.get("text", "")
        images = step.get("images", [])
    else:
        text = str(step)
        images = []

    # Оновити статус в БД
    update_order_stage_db(order_id, stage, status=f"На етапі {stage+1}")

    # Відправити текст
    if text:
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.warning("Не вдалося надіслати текст користувачу %s: %s", user_id, e)

    # Відправити зображення (шлях або file_id)
    for img in images:
        try:
            if isinstance(img, str) and os.path.exists(img):
                with open(img, "rb") as f:
                    await context.bot.send_photo(chat_id=user_id, photo=f)
            else:
                # Якщо передано file_id або посилання, bot спробує відправити так
                await context.bot.send_photo(chat_id=user_id, photo=img)
        except Exception as e:
            logger.warning("Не вдалося відправити зображення %s користувачу %s: %s", img, user_id, e)

# ====== Хендлери меню / логіка ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Актуальні банки", callback_data="menu_banks")],
        [InlineKeyboardButton("Інформація та оплата", callback_data="menu_info")],
        [InlineKeyboardButton("Подати заявку на співпрацю", callback_data="menu_coop")]
    ]
    if update.message:
        await update.message.reply_text("Вітаємо! Оберіть опцію:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text("Вітаємо! Оберіть опцію:", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            pass

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_banks":
        keyboard = [
            [InlineKeyboardButton("Реєстрація", callback_data="type_register")],
            [InlineKeyboardButton("Перевʼязка", callback_data="type_change")],
            [InlineKeyboardButton("Назад", callback_data="back_to_main")]
        ]
        await query.edit_message_text("Оберіть тип операції:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "menu_info":
        info_text = (
            "💳 Оплата здійснюється на карту XXXX XXXX XXXX XXXX\n"
            "Після оплати обов’язково відправте квитанцію в чат.\n\n"
            "Якщо маєте питання, звертайтесь до менеджера."
        )
        await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Назад", callback_data="back_to_main")]]
        ))
        return

    if data == "back_to_main":
        await start(update, context)
        return

    if data in ("type_register", "type_change"):
        action = "register" if data == "type_register" else "change"
        banks = BANKS_REGISTER if action == "register" else BANKS_CHANGE
        keyboard = [[InlineKeyboardButton(bank, callback_data=f"bank_{bank}_{action}")] for bank in banks]
        keyboard.append([InlineKeyboardButton("Назад", callback_data="menu_banks")])
        if not banks:
            await query.edit_message_text("Наразі записи для цього типу відсутні. Спробуйте пізніше.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="menu_banks")]]))
            return
        await query.edit_message_text("Оберіть банк:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("bank_"):
        try:
            _, bank, action = data.split("_", 2)
        except ValueError:
            await query.edit_message_text("❌ Некоректний вибір. Спробуйте ще раз.")
            return

        user_id = query.from_user.id
        age_required = find_age_requirement(bank, action)
        user_states[user_id] = {"order_id": None, "bank": bank, "action": action, "stage": 0, "age_required": age_required}

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Так, я відповідаю вимогам", callback_data="age_confirm_yes"),
             InlineKeyboardButton("Ні, я не підходжу", callback_data="age_confirm_no")]
        ])
        text = f"Ви обрали банк {bank} ({'Реєстрація' if action == 'register' else 'Перевʼязка'}).\n"
        if age_required:
            text += f"📅 Вимога: мінімальний вік — {age_required} років.\n"
        text += "Підтвердіть, будь ласка, що ви відповідаєте цим вимогам."
        await query.edit_message_text(text, reply_markup=keyboard)
        return

# ====== Підтвердження віку ======
async def age_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    state = user_states.get(user_id)
    if not state:
        await query.edit_message_text("❌ Сталася помилка, будь ласка, почніть заново командою /start")
        return

    if data == "age_confirm_no":
        keyboard = [[InlineKeyboardButton(bank, callback_data=f"bank_{bank}_register")] for bank in BANKS_REGISTER] + \
                   [[InlineKeyboardButton(bank, callback_data=f"bank_{bank}_change")] for bank in BANKS_CHANGE]
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_main")])
        await query.edit_message_text("Ви не відповідаєте віковим вимогам. Будь ласка, оберіть інший банк.", reply_markup=InlineKeyboardMarkup(keyboard))
        user_states.pop(user_id, None)
        return

    bank = state["bank"]
    action = state["action"]
    username = query.from_user.username or "Без_ніка"

    order_id = create_order_in_db(user_id, username, bank, action)
    user_states[user_id].update({"order_id": order_id, "stage": 0})

    assigned = await assign_group_or_queue(order_id, user_id, username, bank, action, context)
    if not assigned:
        await query.edit_message_text("⏳ Усі менеджери зайняті. Ви поставлені в чергу. Отримаєте повідомлення коли звільниться місце.")
        return

    await send_instruction(user_id, context)
    await query.edit_message_text("✅ Вік підтверджено. Починаємо інструкції.")

# ====== Обробка фото від користувача ======
async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.message.from_user
    user_id = user.id
    username = user.username or "Без_ніка"
    state = user_states.get(user_id)

    if not state:
        await update.message.reply_text("Спочатку оберіть банк командою /start")
        return

    stage = state.get("stage", 0)
    order_id = state.get("order_id")
    if not order_id:
        cursor.execute("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        r = cursor.fetchone()
        if not r:
            await update.message.reply_text("Помилка: замовлення не знайдено в базі.")
            return
        order_id = r[0]
        user_states[user_id]["order_id"] = order_id

    file_id = update.message.photo[-1].file_id
    cursor.execute("INSERT INTO order_photos (order_id, stage, file_id) VALUES (?, ?, ?)", (order_id, stage + 1, file_id))
    conn.commit()

    cursor.execute("UPDATE orders SET status=?, stage=? WHERE id=?", (f"Очікує перевірки (етап {stage+1})", stage, order_id))
    conn.commit()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{user_id}"),
         InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{user_id}")],
        [InlineKeyboardButton("💬 Звʼязатися з клієнтом", url=f"https://t.me/{username}")]
    ])

    caption = (
        f"📌 <b>Перевірка скріну</b>\n"
        f"👤 Користувач: @{username} (ID: {user_id})\n"
        f"🏦 Банк: {state.get('bank')}\n"
        f"🔄 Операція: {state.get('action')}\n"
        f"📍 Етап: {stage+1}\n"
    )

    try:
        await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.warning("Не вдалося переслати фото в адмін-групу: %s", e)

    await update.message.reply_text("✅ Ваші скріни на перевірці. Очікуйте відповідь.")

# ====== Дії адміна: approve/reject ======
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data

    try:
        action, str_user_id = raw.split("_", 1)
        user_id = int(str_user_id)
    except Exception:
        try:
            await query.edit_message_caption(caption="⚠️ Некоректні дані.")
        except Exception:
            pass
        return

    if user_id not in user_states:
        cursor.execute("SELECT id, bank, action, stage FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        r = cursor.fetchone()
        if r:
            order_id, bank, action_db, stage = r
            user_states[user_id] = {"order_id": order_id, "bank": bank, "action": action_db, "stage": stage, "age_required": find_age_requirement(bank, action_db)}
        else:
            try:
                await query.edit_message_caption(caption="⚠️ Користувача не знайдено в сесії")
            except Exception:
                pass
            return

    if action == "approve":
        user_states[user_id]["stage"] += 1
        order_id = user_states[user_id].get("order_id")
        if order_id:
            update_order_stage_db(order_id, user_states[user_id]["stage"])
        await send_instruction(user_id, context)
        try:
            await query.edit_message_caption(caption="✅ Етап підтверджено")
        except Exception:
            pass

    elif action == "reject":
        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Етап {user_states[user_id]['stage']+1} відхилено. Надішліть скріни повторно.")
        except Exception:
            logger.warning("Не вдалося повідомити користувача про відмову.")
        try:
            await query.edit_message_caption(caption="❌ Етап відхилено")
        except Exception:
            pass

# ====== Cooperation handlers ======
async def cooperation_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✍ Напишіть, будь ласка, вашу заявку у наступному повідомленні.")
    return COOPERATION_INPUT

async def cooperation_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    text = update.message.text
    cursor.execute("INSERT INTO cooperation_requests (user_id, username, text) VALUES (?, ?, ?)", (user.id, user.username, text))
    conn.commit()

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Звʼязатися з клієнтом", url=f"https://t.me/{user.username or user.id}")]])
    msg = f"📩 Нова заявка на співпрацю\n👤 Користувач: @{user.username or 'Без ника'} (ID: {user.id})\n📝 Текст заявки:\n{text}"
    try:
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=msg, reply_markup=keyboard)
    except Exception:
        logger.warning("Не вдалося переслати заявку на співпрацю в адмін-групу.")
    await update.message.reply_text("✅ Ваша заявка прийнята. Ми з вами зв'яжемося найближчим часом.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Введення заявки скасовано.")
    return ConversationHandler.END

# ====== Admin: history ======
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас немає прав для цієї команди.")
        return

    args = context.args
    if not args:
        cursor.execute("SELECT id, user_id, username, bank, action, status FROM orders ORDER BY id DESC LIMIT 10")
        orders = cursor.fetchall()
        if not orders:
            await update.message.reply_text("📭 Замовлень немає.")
            return
        text = "📋 <b>Останні 10 замовлень:</b>\n\n"
        for o in orders:
            text += f"🆔 OrderID: {o[0]}\n👤 UserID: {o[1]} (@{o[2]})\n🏦 {o[3]} — {o[4]}\n📍 {o[5]}\n\n"
        await update.message.reply_text(text, parse_mode="HTML")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Невірний формат ID.")
        return

    cursor.execute("SELECT id, bank, action FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (target_id,))
    order = cursor.fetchone()
    if not order:
        await update.message.reply_text("❌ Замовлень для цього користувача не знайдено.")
        return

    order_id = order[0]
    bank = order[1]
    action = order[2]
    await update.message.reply_text(f"📂 Історія замовлення:\n🏦 {bank} — {action}", parse_mode="HTML")

    cursor.execute("SELECT stage, file_id FROM order_photos WHERE order_id=? ORDER BY stage ASC", (order_id,))
    photos = cursor.fetchall()
    for stage, file_id in photos:
        try:
            await update.message.reply_photo(photo=file_id, caption=f"Етап {stage}")
        except Exception:
            logger.warning("Не вдалося відправити фото історії адміну.")

# ====== Admin: groups management ======
async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Немає доступу")
    if len(context.args) < 2:
        return await update.message.reply_text("Використання: /addgroup <group_id> <назва>")
    try:
        group_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ ID групи має бути числом")
    name = " ".join(context.args[1:])
    cursor.execute("INSERT OR IGNORE INTO manager_groups (group_id, name) VALUES (?, ?)", (group_id, name))
    conn.commit()
    await update.message.reply_text(f"✅ Групу '{name}' додано")

async def del_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Немає доступу")
    if not context.args:
        return await update.message.reply_text("Використання: /delgroup <group_id>")
    try:
        group_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ ID групи має бути числом")
    cursor.execute("DELETE FROM manager_groups WHERE group_id=?", (group_id,))
    conn.commit()
    await update.message.reply_text("✅ Групу видалено")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Немає доступу")
    cursor.execute("SELECT group_id, name, busy FROM manager_groups ORDER BY id ASC")
    groups = cursor.fetchall()
    if not groups:
        return await update.message.reply_text("📭 Немає груп")
    text = "📋 Список груп:\n"
    for gid, name, busy in groups:
        text += f"• {name} ({gid}) — {'🔴 Зайнята' if busy else '🟢 Вільна'}\n"
    await update.message.reply_text(text)

# ====== Admin: queue viewing ======
async def show_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ У вас немає прав для цієї команди.")
    cursor.execute("SELECT id, user_id, username, bank, action, created_at FROM queue ORDER BY id ASC")
    rows = cursor.fetchall()
    if not rows:
        return await update.message.reply_text("📭 Черга пуста.")
    text = "📋 Черга:\n\n"
    for r in rows:
        text += f"#{r[0]} — @{r[2]} (ID: {r[1]}) — {r[3]} / {r[4]} — {r[5]}\n"
    await update.message.reply_text(text)

# ====== User helper: status ======
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    order = get_last_order_for_user(user_id)
    if not order:
        await update.message.reply_text("У вас немає активних замовлень.")
        return
    order_id, bank, action, stage, status_text, group_id = order
    text = f"📌 OrderID: {order_id}\n🏦 {bank} — {action}\n📍 {status_text}\nЕтап: {stage+1}"
    await update.message.reply_text(text)

# ====== Error handler ======
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка в аплікації: %s", context.error)
    try:
        if update and getattr(update, "effective_chat", None):
            await update.effective_chat.send_message("⚠️ Сталася технічна помилка. Ми вже працюємо над виправленням.")
    except Exception:
        pass

# ====== Регістрація handler-ів і запуск ======
def main():
    if BOT_TOKEN in ("", "CHANGE_ME_PLEASE"):
        print("ERROR: BOT_TOKEN не встановлено. Задайте змінну середовища BOT_TOKEN.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^(menu_banks|menu_info|back_to_main|type_register|type_change|bank_.*)$"))
    app.add_handler(CallbackQueryHandler(age_confirm_handler, pattern="^age_confirm_.*$"))
    app.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(approve|reject)_.*$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cooperation_start_handler, pattern="menu_coop")],
        states={COOPERATION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cooperation_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_message=True
    )
    app.add_handler(conv_handler)

    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("addgroup", add_group))
    app.add_handler(CommandHandler("delgroup", del_group))
    app.add_handler(CommandHandler("groups", list_groups))
    app.add_handler(CommandHandler("queue", show_queue))
    app.add_handler(CommandHandler("status", status))

    logger.info("Бот запущений...")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass