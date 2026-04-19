# app/scheduler.py
import os
import time
import random
import hashlib
import logging
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from app.db import get_conn, init_db
from app.telegram import send_media, send_text, get_next_delay

# Настройка логирования
logger = logging.getLogger(__name__)

# Пути внутри контейнера (с поддержкой переменных окружения)
BASE_MEDIA = Path(os.getenv("MEDIA_PATH", "/app/media"))
BASE_PROCESSED = Path(os.getenv("MEDIA_PATH", "/app/media")) / "sended"

def get_file_hash(filepath):
    """Вычисляет MD5-хэш файла для дедупликации"""
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        print(f"⚠️ Ошибка хэша: {e}")
        return None

def get_files_from_queue(source_path):
    """Возвращает список файлов из папки + всех подпапок (рекурсивно), исключая файлы из sended"""
    if not source_path:
        return []
    
    path = BASE_MEDIA / source_path
    if not path.exists():
        logger.warning(f"⚠️ Папка не найдена: {path}")
        return []
    
    valid_ext = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webm", ".mkv", ".mp3", ".wav", ".ogg", ".flac", ".avif"}
    
    files = []
    for f in path.rglob("*"):
        # Пропускаем файлы из папки sended - ПРОВЕРЯЕМ ЯВНО ЧТО ЭТО ДОЧЕРНЯЯ ПАПКА
        if BASE_PROCESSED in f.parents:
            continue
        if f.is_file() and f.suffix.lower() in valid_ext:
            files.append(str(f))
    
    return sorted(files)  # Сортируем для консистентности

def generate_caption(queue_name, filepath, source_path):
    """Генерирует подпись: ВСЕ теги из подпапок + тип файла"""
    ext = Path(filepath).suffix.lower()
    
    # Теги по типу файла
    type_tags = {
        ".mp4": "#vid", ".mkv": "#vid", ".webm": "#vid",
        ".gif": "#gif",
        ".mp3": "#music", ".wav": "#music", ".ogg": "#music", ".flac": "#music"
    }
    
    # Извлекаем ВСЕ теги из подпапок относительно source_path
    # НОРМАЛИЗАЦИЯ: убираем пробелы, заменяем недопустимые символы на _, приводим к нижнему регистру
    folder_tags = []
    if source_path and filepath:
        try:
            file_path = Path(filepath)
            source_path_obj = BASE_MEDIA / source_path
            rel_path = file_path.relative_to(source_path_obj)
            rel_parts = rel_path.parts
            # Берём все части КРОМЕ имени файла (последний элемент)
            for part in rel_parts[:-1]:
                if part and not part.startswith('.'):
                    # Нормализация: replace пробелов на _, удаляем спецсимволы, lower()
                    normalized = part.replace(' ', '_').lower()
                    # Оставляем только буквы, цифры и _
                    normalized = ''.join(c if c.isalnum() or c == '_' else '_' for c in normalized)
                    # Убираем множественные _
                    while '__' in normalized:
                        normalized = normalized.replace('__', '_')
                    normalized = normalized.strip('_')
                    if normalized:
                        folder_tags.append(f"#{normalized}")
        except ValueError:
            # Не удалось вычислить относительный путь — пропускаем теги
            pass
    
    # Добавляем тип-тег
    type_tag = type_tags.get(ext, "")
    
    # Формируем финальные теги
    all_tags = [t for t in folder_tags + [type_tag] if t]
    tags_str = " ".join(all_tags) if all_tags else ""
    
    # Фолбэк: если совсем пусто — пустая строка
    caption = tags_str.strip()
    
    logger.debug(f"📝 Подпись: '{caption}' (папки={' '.join(folder_tags) or '—'}, тип={type_tag or '—'})")
    return caption

def move_to_sended(filepath, source_path):
    """Перемещает файл в /app/media/sended с сохранением структуры"""
    try:
        file_path = Path(filepath)
        rel_path = file_path.relative_to(BASE_MEDIA)
        dest = BASE_PROCESSED / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        if file_path.exists() and not dest.exists():
            file_path.rename(dest)
            logger.info(f"✅ Перемещено в sended: {dest}")
            return True
    except Exception as e:
        logger.error(f"⚠️ Не удалось переместить: {e}")
    return False

def auto_switch_queues():
    """Управление очередями: ОДНА активная на КАЖДЫЙ канал"""
    logger.debug("🔄 [AUTO_SWITCH] Проверка...")
    
    with get_conn() as conn:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Завершаем по времени
        for q in conn.execute("SELECT id, channel_id FROM queues WHERE end_time<=? AND status IN ('active','queued')", (now,)).fetchall():
            conn.execute("UPDATE queues SET status='ended' WHERE id=?", (q["id"],))
            logger.info(f"  ⏰ Очередь #{q['id']} завершена")
            _promote_next_queue(conn, q["channel_id"])
        
        # 2. Принудительный запуск
        for q in conn.execute("SELECT id, channel_id FROM queues WHERE force_active=1 AND status='pending'").fetchall():
            _force_activate_queue(conn, q["channel_id"], q["id"])
        
        # 3. Автоматический старт — ПРОВЕРЯЕМ КАЖДЫЙ КАНАЛ ОТДЕЛЬНО
        channels_with_pending = conn.execute("""
            SELECT DISTINCT channel_id FROM queues WHERE status='pending' AND start_time<=?
        """, (now,)).fetchall()
        
        for ch in channels_with_pending:
            channel_id = ch["channel_id"]
            # Проверяем, есть ли уже активная очередь для этого канала
            active = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='active'", (channel_id,)).fetchone()
            if not active:
                # Канал свободен — берём первую готовую очередь
                ready = conn.execute("""
                    SELECT id FROM queues 
                    WHERE channel_id=? AND status='pending' AND start_time<=?
                    ORDER BY queue_order ASC LIMIT 1
                """, (channel_id, now)).fetchone()
                if ready:
                    conn.execute("UPDATE queues SET status='active' WHERE id=?", (ready["id"],))
                    logger.info(f"  🟢 Очередь #{ready['id']} активирована (канал {channel_id})")
        
        # 4. Продвижение очередей со статуса 'queued' в 'active' при освобождении канала
        for ch_row in conn.execute("SELECT DISTINCT channel_id FROM queues"):
            channel_id = ch_row["channel_id"]
            active = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='active'", (channel_id,)).fetchone()
            if not active:
                # Нет активной - пробуем активировать первую из queued
                next_q = conn.execute("""
                    SELECT id FROM queues 
                    WHERE channel_id=? AND status='queued' 
                    ORDER BY queue_order ASC LIMIT 1
                """, (channel_id,)).fetchone()
                if next_q:
                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("UPDATE queues SET status='active', queue_order=0, actual_start_time=? WHERE id=?", (now, next_q["id"]))
                    logger.info(f"  🟢 Очередь #{next_q['id']} активирована из queued (канал {channel_id})")
        
        conn.commit()
        
def _try_activate_queue(conn, channel_id, queue_id):
    """Пытается активировать очередь, если канал свободен"""
    active = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='active'", (channel_id,)).fetchone()
    if active:
        # Канал занят — ставим в очередь
        conn.execute("""
            UPDATE queues SET status='queued', queue_order=(
                SELECT COALESCE(MAX(queue_order),0)+1 FROM queues 
                WHERE channel_id=? AND status IN ('queued','active')
            ) WHERE id=?
        """, (channel_id, queue_id))
        logger.info(f"  🟡 Очередь #{queue_id} в очереди (канал {channel_id})")
    else:
        # Активируем и записываем фактическое время старта
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE queues SET status='active', actual_start_time=? WHERE id=?", (now, queue_id))
        logger.info(f"  🟢 Очередь #{queue_id} активирована (канал {channel_id})")
        
def _force_activate_queue(conn, channel_id, queue_id):
    """Принудительный запуск — ставит на паузу текущую очередь канала"""
    current = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='active'", (channel_id,)).fetchone()
    if current:
        conn.execute("UPDATE queues SET status='paused', prev_queue_id=? WHERE id=?", (queue_id, current["id"]))
        logger.info(f"  ⏸ Очередь #{current['id']} приостановлена")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE queues SET status='active', force_active=1, queue_order=0, actual_start_time=? WHERE id=?", (now, queue_id))
    logger.info(f"  ⚡ Очередь #{queue_id} запущена принудительно")

def _promote_next_queue(conn, channel_id):
    """Продвигает следующую очередь для канала"""
    # Сначала пробуем вернуть предыдущую
    prev = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='paused' AND prev_queue_id>0", (channel_id,)).fetchone()
    if prev:
        # Возвращаем предыдущую очередь с сохранением actual_start_time (не меняем его)
        conn.execute("UPDATE queues SET status='active' WHERE id=?", (prev["id"],))
        logger.info(f"  ↩️ Возврат к очереди #{prev['id']}")
        return
    
    # Иначе берём из queued
    next_q = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='queued' ORDER BY queue_order ASC LIMIT 1", (channel_id,)).fetchone()
    if next_q:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE queues SET status='active', queue_order=0, actual_start_time=? WHERE id=?", (now, next_q["id"]))
        logger.info(f"  🟢 Очередь #{next_q['id']} активирована")
    
    # Если нет queued, проверяем paused без prev_queue_id (после принудительного завершения)
    # и активируем первую pending если есть
    if not prev and not next_q:
        pending = conn.execute("""
            SELECT id FROM queues 
            WHERE channel_id=? AND status='pending' 
            ORDER BY start_time ASC LIMIT 1
        """, (channel_id,)).fetchone()
        if pending:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE queues SET status='active', actual_start_time=? WHERE id=?", (now, pending["id"]))
            logger.info(f"  🟢 Очередь #{pending['id']} активирована из pending")
        
def process_queues():
    """Проходит по активным очередям и отправляет посты (с учётом интервалов)"""
    logger.info("🔄 [PROCESS] Проверка очередей...")
    
    with get_conn() as conn:
        queues = conn.execute("SELECT * FROM queues WHERE status='active'").fetchall()
        logger.info(f"  📋 Найдено активных очередей: {len(queues)}")
        
        now = time.time()
        
        for q in queues:
            logger.info(f"\n  📮 Обработка очереди #{q['id']}: {q['name']}")
            logger.info(f"     Источник: {q['source_path']}")
            logger.info(f"     Интервал: {q['interval_sec']}с + {q['jitter_sec']}с")
            
            # Проверяем, не слишком ли рано для этой очереди
            next_send_time = q['next_send_time']
            if next_send_time is not None and now < next_send_time:
                wait_seconds = int(next_send_time - now)
                logger.info(f"    ⏳ Ещё рано, ждём {wait_seconds} сек...")
                continue
            
            if not q["channel_id"]: 
                logger.warning("    ⚠️ Нет channel_id")
                continue
            
            ch = conn.execute("SELECT chat_id, name FROM channels WHERE id=?", (q["channel_id"],)).fetchone()
            if not ch: 
                logger.warning("    ⚠️ Канал не найден")
                continue
            
            logger.info(f"    📡 Канал: {ch['name']} ({ch['chat_id']})")
            
            # Получаем файлы (уже отсортированы в get_files_from_queue)
            files = get_files_from_queue(q["source_path"])
            
            if not files:
                logger.warning("    ⚠️ Файлов нет — завершаем очередь")
                conn.execute("UPDATE queues SET status='ended' WHERE id=?", (q["id"],))
                conn.commit()
                continue
            
            # Ищем первый НЕ отправленный файл по хэшу
            next_file = None
            file_hash = None
            for filepath in files:
                file_hash = get_file_hash(filepath)
                if file_hash:
                    already = conn.execute("""
                        SELECT id FROM post_log 
                        WHERE file_hash=? AND channel_name=? AND status='sent'
                    """, (file_hash, ch["name"])).fetchone()
                    if not already:
                        next_file = filepath
                        break
            
            if not next_file:
                logger.info("    ✅ Все файлы отправлены — завершаем очередь")
                conn.execute("UPDATE queues SET status='ended' WHERE id=?", (q["id"],))
                conn.commit()
                continue
            
            filepath = next_file
            logger.info(f"    📤 Отправка файла: {filepath}")
            
            # Генерируем подпись
            caption = generate_caption(q["name"], filepath, q["source_path"])
            
            # Отправляем
            logger.info(f"    🚀 Отправка в Telegram...")
            result = send_media(ch["chat_id"], filepath, caption=caption)
            success = result[0]
            err = result[1] if len(result) > 1 else None
            
            if success:
                logger.info(f"    ✅ Успешно отправлено!")
            else:
                logger.error(f"    ❌ Ошибка: {err}")
            
            status = "sent" if success else "error"
            
            # Логируем - используем уже вычисленный хэш
            conn.execute("""
                INSERT INTO post_log (queue_id, channel_name, file_type, scheduled_at, sent_at, status, error, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (q["id"], ch["name"], Path(filepath).suffix, 
                  time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%Y-%m-%d %H:%M:%S"), 
                  status, err, file_hash))
            
            if success:
                move_to_sended(filepath, q["source_path"])
            
            conn.commit()
            
            # Вычисляем и сохраняем время следующей отправки для этой очереди
            delay = get_next_delay(q["interval_sec"], q["jitter_sec"])
            next_time = now + delay
            conn.execute("UPDATE queues SET next_send_time=? WHERE id=?", (next_time, q["id"]))
            conn.commit()
            
            logger.info(f"    ⏱ Пауза {delay} сек перед следующим постом (след. отправка в {time.strftime('%H:%M:%S', time.localtime(next_time))})...")

# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(auto_switch_queues, "interval", seconds=30, id="auto_switch")
scheduler.add_job(process_queues, "interval", seconds=10, id="process_queues")

def start_scheduler():
    init_db()
    scheduler.start()
    logger.info("🔄 Планировщик запущен (проверка каждые 10 сек)")

def stop_scheduler():
    """Graceful shutdown планировщика"""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("🛑 Планировщик остановлен")
