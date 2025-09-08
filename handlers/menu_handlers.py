from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from states import BANKS_REGISTER, BANKS_CHANGE, user_states, find_age_requirement
from db import ADMIN_GROUP_ID
from handlers.photo_handlers import create_order_in_db, assign_group_or_queue, send_instruction

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
        await query.edit_message_text("⏳ Усі менеджери зайняті. Ви поставлені в чергу. Отримаєте повідомлення коли звільниться менеджер.")
        return

    await send_instruction(user_id, context)
    await query.edit_message_text("✅ Вік підтверджено. Починаємо інструкції.")