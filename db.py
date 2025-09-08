import os
import sys
import sqlite3
import logging

BOT_TOKEN = "8303921633:AAFu3nvim6qggmkIq2ghg5EMrT-8RhjoP50"
ADMIN_GROUP_ID = -4930176305
ADMIN_ID = 7797088374
LOCK_FILE = "bot.lock"
DB_FILE = os.getenv("DB_FILE", "orders.db")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if os.path.exists(LOCK_FILE):
    print("⚠️ bot.lock виявлено — ймовірно бот вже запущений. Завершую роботу.")
    sys.exit(1)
open(LOCK_FILE, "w").close()

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

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
    file_id TEXT,
    confirmed INTEGER DEFAULT 0
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