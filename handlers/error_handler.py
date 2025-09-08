from db import logger

async def error_handler(update: object, context):
    logger.exception("Ошибка в аплікації: %s", context.error)
    try:
        if update and getattr(update, "effective_chat", None):
            await update.effective_chat.send_message("⚠️ Сталася технічна помилка. Ми вже працюємо над виправленням.")
    except Exception:
        pass