# app/db.py
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/app/data/poster.db")

def ensure_db_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_conn():
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_setting(key, default=None):
    """Получение настройки из БД"""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def save_setting(key, value):
    """Сохранение настройки в БД"""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
        """, (key, value))
        conn.commit()

def get_allowed_users():
    """Получает список разрешённых user_id для уведомлений"""
    with get_conn() as conn:
        rows = conn.execute("SELECT value FROM settings WHERE key LIKE 'allowed_user_%'").fetchall()
        return [{"user_id": row["value"]} for row in rows]

def add_allowed_user(user_id):
    """Добавляет разрешённого user_id"""
    with get_conn() as conn:
        # Проверяем, нет ли уже такого пользователя
        existing = conn.execute("SELECT value FROM settings WHERE key LIKE 'allowed_user_%' AND value=?", (user_id,)).fetchone()
        if not existing:
            # Находим следующий доступный ключ
            max_idx = conn.execute("SELECT MAX(CAST(SUBSTR(key, 15) AS INTEGER)) FROM settings WHERE key LIKE 'allowed_user_%'").fetchone()[0]
            next_idx = (max_idx or 0) + 1
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (f"allowed_user_{next_idx}", str(user_id)))
            conn.commit()
            return True
    return False

def remove_allowed_user(user_id):
    """Удаляет разрешённого user_id"""
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'allowed_user_%' AND value=?", (user_id,))
        conn.commit()

def clear_sent_files_for_channel(channel_id):
    """Очищает логи отправленных файлов для канала"""
    with get_conn() as conn:
        # Получаем все очереди этого канала
        queues = conn.execute("SELECT id FROM queues WHERE channel_id=?", (channel_id,)).fetchall()
        queue_ids = [q["id"] for q in queues]
        if queue_ids:
            placeholders = ','.join('?' * len(queue_ids))
            conn.execute(f"DELETE FROM post_log WHERE queue_id IN ({placeholders})", queue_ids)
            conn.commit()
            return len(queue_ids)
    return 0

def init_db():
    """Создание таблиц если они не существуют (без сброса данных)"""
    ensure_db_dir()
    
    with get_conn() as conn:
        # === channels ===
        conn.execute("""CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            chat_id TEXT UNIQUE NOT NULL,
            name TEXT,
            active INTEGER DEFAULT 1
        )""")
        
        # === queues ===
        conn.execute("""CREATE TABLE IF NOT EXISTS queues (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            name TEXT NOT NULL,
            source_type TEXT DEFAULT 'local',
            source_path TEXT DEFAULT '',
            start_time DATETIME,
            end_time DATETIME,
            interval_sec INTEGER DEFAULT 600,
            jitter_sec INTEGER DEFAULT 30,
            status TEXT DEFAULT 'pending',
            queue_order INTEGER DEFAULT 999,
            force_active INTEGER DEFAULT 0,
            prev_queue_id INTEGER DEFAULT 0,
            actual_start_time DATETIME,
            next_send_time REAL,
            FOREIGN KEY (channel_id) REFERENCES channels(id)
        )""")
        
        # === post_log ===
        conn.execute("""CREATE TABLE IF NOT EXISTS post_log (
            id INTEGER PRIMARY KEY,
            queue_id INTEGER,
            channel_name TEXT,
            file_type TEXT,
            scheduled_at DATETIME,
            sent_at DATETIME,
            status TEXT,
            error TEXT,
            file_hash TEXT,
            filename TEXT,
            FOREIGN KEY (queue_id) REFERENCES queues(id)
        )""")
        
        # === settings ===
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        
        conn.commit()
    
    print(f"✅ База данных инициализирована: {DB_PATH}")
    print("✅ Таблицы: channels, queues, post_log, settings")

# Вызываем при импорте модуля
init_db()
