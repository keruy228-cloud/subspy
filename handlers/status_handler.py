from telegram import Update
from telegram.ext import ContextTypes
from handlers.photo_handlers import get_last_order_for_user

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    order = get_last_order_for_user(user_id)
    if not order:
        await update.message.reply_text("У вас немає активних замовлень.")
        return
    order_id, bank, action, stage, status_text, group_id = order
    text = f"📌 OrderID: {order_id}\n🏦 {bank} — {action}\n📍 {status_text}\nЕтап: {stage+1}"
    await update.message.reply_text(text)