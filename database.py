import sqlite3

DB_NAME = "mining_bot.db"

def get_db():
    return sqlite3.connect(DB_NAME)

def create_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        balance REAL DEFAULT 0,
        mining INTEGER DEFAULT 0,
        referred_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def add_user(telegram_id, username, referred_by=None):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    INSERT OR IGNORE INTO users
    (telegram_id, username, referred_by)
    VALUES (?, ?, ?)
    """, (telegram_id, username, referred_by))

    conn.commit()
    conn.close()
