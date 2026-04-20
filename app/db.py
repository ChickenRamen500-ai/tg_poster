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
