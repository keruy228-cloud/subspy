```markdown
# subspybot

Telegram-бот для обробки замовлень (реєстрація/перев'язка) з простим UI для користувачів та інструментами адміністрування.

## Налаштування
1. Створіть віртуальне оточення і встановіть залежності:

```bash
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot --upgrade
```

2. Налаштуйте змінні середовища:

```bash
export BOT_TOKEN="<your-bot-token>"
export ADMIN_ID="<admin-user-id>"
export ADMIN_GROUP_ID="<admin-group-chat-id>"
```

3. Підготуйте файл `instructions.py` — у репозиторії викладено приклад.

4. Запуск бота:

```bash
python3 client_bot.py
```

## Команди
- /start — головне меню
- /status — статус вашого останнього замовлення
- /history — (тільки адмін) останні 10 замовлень
- /addgroup <group_id> <name> — (адмін) додати групу менеджерів
- /delgroup <group_id> — (адмін) видалити групу
- /groups — (адмін) список груп
- /queue — (адмін) подивитись чергу

---

Примітки:
- Переконайтесь, що BOT_TOKEN і ADMIN_ID/ADMIN_GROUP_ID вказані в середовищі.
- instructions.py визначає логіку та етапи — налаштуйте її під свої банки.
```