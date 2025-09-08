from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from db import cursor, conn, ADMIN_GROUP_ID
from states import COOPERATION_INPUT

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
        pass
    await update.message.reply_text("✅ Ваша заявка прийнята. Ми з вами зв'яжемося найближчим часом.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Введення заявки скасовано.")
    return ConversationHandler.END