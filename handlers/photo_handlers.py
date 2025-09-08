import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from db import cursor, conn, ADMIN_GROUP_ID, logger
from states import user_states, find_age_requirement

REJECT_REASON, MANAGER_MESSAGE = range(2)

# Для запобігання дублюванню фото при надсиланні альбому
pending_albums = {}  # media_group_id: {"user_id", "order_id", "stage", "photos"}

def create_order_in_db(user_id: int, username: str, bank: str, action: str) -> int:
    cursor.execute(
        "INSERT INTO orders (user_id, username, bank, action, stage, status) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, bank, action, 0, "На етапі 1")
    )
    conn.commit()
    return cursor.lastrowid

def update_order_stage_db(order_id: int, new_stage: int, status: str = None):
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

def get_free_groups(limit: int = None):
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

def get_last_order_for_user(user_id: int):
    cursor.execute("SELECT id, bank, action, stage, status, group_id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    return cursor.fetchone()

def get_order_by_id(order_id: int):
    cursor.execute("SELECT id, user_id, username, bank, action, stage, status, group_id FROM orders WHERE id=?", (order_id,))
    return cursor.fetchone()

def get_photos_for_order_stage(order_id: int, stage: int):
    cursor.execute("SELECT id, file_id, confirmed FROM order_photos WHERE order_id=? AND stage=?", (order_id, stage))
    return cursor.fetchall()

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
                await context.bot.send_message(chat_id=user_id, text="⏳ Усі менеджери зайняті. Ви в черзі. Отримаєте повідомлення, коли звільниться менеджер.")
            except Exception:
                logger.warning("Не вдалося повідомити користувача в черзі (ID=%s)", user_id)
            logger.info("User %s (order %s) enqueued", user_id, order_id)
        except Exception as e:
            logger.exception("assign_group_or_queue error while enqueue: %s", e)
        return False

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

async def send_instruction(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    from states import INSTRUCTIONS
    state = user_states.get(user_id)
    if not state:
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
        update_order_stage_db(order_id, stage, status="Помилка: інструкції відсутні")
        logger.warning("No instructions for %s %s (order %s)", bank, action, order_id)
        try:
            await context.bot.send_message(chat_id=user_id, text="❌ Помилка: інструкції для обраного банку/операції відсутні. Зв'яжіться з адміністратором.")
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"⚠️ Для замовлення {order_id} немає інструкцій: {bank} {action}")
        except Exception:
            pass
        return

    if stage >= len(steps):
        update_order_stage_db(order_id, stage, status="Завершено")
        order = get_order_by_id(order_id)
        if order:
            group_chat_id = order[7]
            if group_chat_id:
                try:
                    free_group_db_by_chatid(group_chat_id)
                except Exception:
                    pass
        try:
            await context.bot.send_message(chat_id=user_id, text="✅ Ваше замовлення завершено. Дякуємо!")
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"✅ Замовлення {order_id} виконано для @{order[2]} (ID: {order[1]})")
        except Exception:
            pass
        user_states.pop(user_id, None)
        try:
            await assign_queued_clients_to_free_groups(context)
        except Exception:
            pass
        return

    step = steps[stage]
    text = step.get("text", "") if isinstance(step, dict) else str(step)
    images = step.get("images", []) if isinstance(step, dict) else []

    update_order_stage_db(order_id, stage, status=f"На етапі {stage+1}")

    if text:
        await context.bot.send_message(chat_id=user_id, text=text)

    for img in images:
        try:
            if isinstance(img, str) and os.path.exists(img):
                with open(img, "rb") as f:
                    await context.bot.send_photo(chat_id=user_id, photo=f)
            else:
                await context.bot.send_photo(chat_id=user_id, photo=img)
        except Exception as e:
            logger.warning("Не вдалося відправити зображення %s користувачу %s: %s", img, user_id, e)

async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media_group_id = getattr(update.message, "media_group_id", None)
    user = update.message.from_user
    user_id = user.id
    username = user.username or "Без_ніка"
    state = user_states.get(user_id)
    stage = state.get("stage", 0) if state else 0

    if not state:
        await update.message.reply_text("Спочатку оберіть банк командою /start")
        return

    order_id = state.get("order_id")
    if not order_id:
        cursor.execute("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        r = cursor.fetchone()
        if not r:
            await update.message.reply_text("Помилка: замовлення не знайдено в базі.")
            return
        order_id = r[0]
        user_states[user_id]["order_id"] = order_id

    # --- АНТИДУБЛЬ для альбомів та кількох фото ---
    # Якщо це альбом
    if media_group_id:
        album_key = f"{user_id}:{order_id}:{stage+1}:{media_group_id}"
        album = pending_albums.setdefault(album_key, [])
        for photo in update.message.photo:
            file_id = photo.file_id
            # Перевірка на дубль у базі
            cursor.execute(
                "SELECT COUNT(*) FROM order_photos WHERE order_id=? AND stage=? AND file_id=?",
                (order_id, stage + 1, file_id)
            )
            already_exists = cursor.fetchone()[0]
            if already_exists:
                continue
            album.append(file_id)
            cursor.execute(
                "INSERT INTO order_photos (order_id, stage, file_id, confirmed) VALUES (?, ?, ?, ?)",
                (order_id, stage + 1, file_id, 0)
            )
            conn.commit()
        # Додаємо лише унікальні фото з альбому
        # Відправити всі фото ОДИН РАЗ після завершення прийому альбому
        # Надсилати фото в групу адміністраторів (по одному або як медіагрупу)
        # Якщо це останнє фото альбому (Telegram сам не дає сигналу), можна просто надсилати по кожному апдейту, але тільки якщо цього file_id ще не було.
        # Надсилаємо фото в групу менеджерів (адмін-групу)
        for idx, file_id in enumerate(album, start=1):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{user_id}_{cursor.lastrowid}"),
                 InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{user_id}_{cursor.lastrowid}")],
                [InlineKeyboardButton("↪️ Пропустити етап", callback_data=f"skip_{user_id}_{stage+1}")],
                [InlineKeyboardButton("🏁 Завершити замовлення", callback_data=f"finish_{user_id}")],
                [InlineKeyboardButton("💬 Написати повідомлення", callback_data=f"msg_{user_id}")]
            ])
            caption = (
                f"📌 <b>Перевірка скріну (альбом, {idx} із {len(album)})</b>\n"
                f"👤 Користувач: @{username} (ID: {user_id})\n"
                f"🏦 Банк: {state.get('bank')}\n"
                f"🔄 Операція: {state.get('action')}\n"
                f"📍 Етап: {stage+1}\n"
            )
            try:
                await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            except Exception as e:
                logger.warning("Не вдалося переслати фото в адмін-групу: %s", e)
        await update.message.reply_text("✅ Ваші скріни (альбом) на перевірці. Очікуйте відповідь.")
        return

    # Якщо це НЕ альбом (одне чи кілька фото)
    photo_ids = [photo.file_id for photo in update.message.photo]
    for idx, file_id in enumerate(photo_ids, start=1):
        # Перевірка на дубль у базі
        cursor.execute(
            "SELECT COUNT(*) FROM order_photos WHERE order_id=? AND stage=? AND file_id=?",
            (order_id, stage + 1, file_id)
        )
        already_exists = cursor.fetchone()[0]
        if already_exists:
            continue
        cursor.execute(
            "INSERT INTO order_photos (order_id, stage, file_id, confirmed) VALUES (?, ?, ?, ?)",
            (order_id, stage + 1, file_id, 0)
        )
        conn.commit()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{user_id}_{cursor.lastrowid}"),
             InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_{user_id}_{cursor.lastrowid}")],
            [InlineKeyboardButton("↪️ Пропустити етап", callback_data=f"skip_{user_id}_{stage+1}")],
            [InlineKeyboardButton("🏁 Завершити замовлення", callback_data=f"finish_{user_id}")],
            [InlineKeyboardButton("💬 Написати повідомлення", callback_data=f"msg_{user_id}")]
        ])
        caption = (
            f"📌 <b>Перевірка скріну ({idx} із {len(photo_ids)})</b>\n"
            f"👤 Користувач: @{username} (ID: {user_id})\n"
            f"🏦 Банк: {state.get('bank')}\n"
            f"🔄 Операція: {state.get('action')}\n"
            f"📍 Етап: {stage+1}\n"
        )
        try:
            await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.warning("Не вдалося переслати фото в адмін-групу: %s", e)

    cursor.execute("UPDATE orders SET status=?, stage=? WHERE id=?",
                   (f"Очікує перевірки (етап {stage+1})", stage, order_id))
    conn.commit()

    await update.message.reply_text("✅ Ваші скріни на перевірці. Очікуйте відповідь.")

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data

    try:
        action, str_user_id, *rest = raw.split("_")
        user_id = int(str_user_id)
    except Exception:
        try:
            await query.edit_message_caption(caption="⚠️ Некоректні дані.")
        except Exception:
            pass
        return

    state = user_states.get(user_id)
    if not state:
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

    order_id = user_states[user_id].get("order_id")

    if action == "approve":
        photo_db_id = int(rest[0])
        cursor.execute("UPDATE order_photos SET confirmed=1 WHERE id=?", (photo_db_id,))
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM order_photos WHERE order_id=? AND stage=? AND confirmed=1", (order_id, user_states[user_id]['stage']+1))
        confirmed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM order_photos WHERE order_id=? AND stage=?", (order_id, user_states[user_id]['stage']+1))
        total = cursor.fetchone()[0]

        message = f"{confirmed} з {total} скрінів підтверджено"
        await update.effective_chat.send_message(message)
        await query.edit_message_caption(caption=f"✅ Скрін підтверджено\n{message}")

    elif action == "reject":
        photo_db_id = int(rest[0])
        context.user_data['reject_user_id'] = user_id
        context.user_data['photo_db_id'] = photo_db_id
        await query.edit_message_caption(caption="❌ Введіть причину відхилення у чат.")
        return REJECT_REASON

    elif action == "skip":
        stage = int(rest[0])
        user_states[user_id]['stage'] += 1
        order_id = user_states[user_id]['order_id']
        update_order_stage_db(order_id, user_states[user_id]['stage'])
        await send_instruction(user_id, context)
        await query.edit_message_caption(caption="↪️ Етап пропущено")

    elif action == "finish":
        await context.bot.send_message(chat_id=user_id, text="🏁 Ваше замовлення було завершене менеджером.")
        await query.edit_message_caption(caption="🏁 Замовлення завершено")

    elif action == "msg":
        context.user_data['msg_user_id'] = user_id
        await query.edit_message_caption(caption="💬 Введіть повідомлення для користувача у чат.")
        return MANAGER_MESSAGE

    return ConversationHandler.END

async def reject_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data['reject_user_id']
    reason = update.message.text
    await update.effective_chat.send_message(f"❌ Ваш скрін був відхилений менеджером.\nПричина: {reason}")
    await update.message.reply_text(f"Причина відхилення збережена: {reason}")
    return ConversationHandler.END

async def manager_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data['msg_user_id']
    message = update.message.text
    await update.effective_chat.send_message(f"💬 Повідомлення менеджера: {message}")
    await update.message.reply_text("✅ Повідомлення відправлено.")
    return ConversationHandler.END