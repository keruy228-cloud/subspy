from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters
)

from db import conn, logger, BOT_TOKEN, LOCK_FILE
from states import COOPERATION_INPUT, REJECT_REASON, MANAGER_MESSAGE
from handlers.menu_handlers import start, main_menu_handler, age_confirm_handler
from handlers.photo_handlers import handle_photos, handle_admin_action, reject_reason_handler, manager_message_handler
from handlers.cooperation_handlers import cooperation_start_handler, cooperation_receive, cancel
from handlers.admin_handlers import (
    history, add_group, del_group, list_groups, show_queue,
    finish_order, finish_all_orders, orders_stats,
    add_admin, remove_admin, list_admins, admin_help
)
from handlers.status_handler import status
from handlers.error_handler import error_handler

def main():
    if BOT_TOKEN in ("", "CHANGE_ME_PLEASE"):
        print("ERROR: BOT_TOKEN не встановлено. Задайте змінну середовища BOT_TOKEN.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^(menu_banks|menu_info|back_to_main|type_register|type_change|bank_.*)$"))
    app.add_handler(CallbackQueryHandler(age_confirm_handler, pattern="^age_confirm_.*$"))
    app.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(approve|reject|skip|finish|msg)_.*$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cooperation_start_handler, pattern="menu_coop")],
        states={
            COOPERATION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cooperation_receive)],
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reject_reason_handler)],
            MANAGER_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, manager_message_handler)]
        },
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
    app.add_handler(CommandHandler("finish_order", finish_order))
    app.add_handler(CommandHandler("finish_all_orders", finish_all_orders))
    app.add_handler(CommandHandler("orders_stats", orders_stats))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("list_admins", list_admins))
    app.add_handler(CommandHandler("help", admin_help))

    logger.info("Бот запущений...")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            import os
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass