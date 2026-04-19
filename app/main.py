# app/main.py
from flask import Flask, render_template, request, redirect, url_for, jsonify
from app.db import get_conn
from app.scheduler import start_scheduler, get_files_from_queue
from app.file_scanner import get_folders_list
import os, time, json, datetime
from pathlib import Path

# Настройка логирования
from logging_config import setup_logging
setup_logging()

BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.jinja_env.autoescape = False

# Хранилище настроек в памяти (в будущем можно перенести в БД)
app_settings = {
    'bot_token': os.getenv("BOT_TOKEN", ""),
    'timezone': os.getenv("TZ", "Asia/Yekaterinburg"),
    'media_path': os.getenv("MEDIA_PATH", "/app/media")
}

@app.route("/")
def dashboard():
    with get_conn() as conn:
        channels = [dict(row) for row in conn.execute("SELECT * FROM channels").fetchall()]
        queues = [dict(row) for row in conn.execute("SELECT * FROM queues").fetchall()]
    return render_template("dashboard.html", channels=channels, queues=queues)

@app.route("/settings")
def settings():
    """Страница настроек приложения"""
    with get_conn() as conn:
        channels = [dict(row) for row in conn.execute("SELECT * FROM channels").fetchall()]
    
    # Список популярных часовых поясов
    timezones = [
        "UTC", "Europe/Moscow", "Europe/London", "Europe/Berlin", "Europe/Paris",
        "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Krasnoyarsk", "Asia/Irkutsk",
        "Asia/Yakutsk", "Asia/Vladivostok", "Asia/Magadan", "Asia/Kamchatka",
        "America/New_York", "America/Chicago", "America/Los_Angeles",
        "Asia/Tokyo", "Asia/Shanghai", "Asia/Singapore", "Australia/Sydney"
    ]
    
    return render_template("settings.html", 
                          bot_token=app_settings.get('bot_token', ''),
                          current_tz=app_settings.get('timezone', 'Asia/Yekaterinburg'),
                          media_path=app_settings.get('media_path', '/app/media'),
                          timezones=timezones,
                          channels=channels)

@app.route("/api/settings/bot_token", methods=["POST"])
def save_bot_token():
    """Сохранение токена бота"""
    token = request.form.get("bot_token", "").strip()
    if token:
        app_settings['bot_token'] = token
        # Обновляем переменную окружения для telegram.py
        os.environ["BOT_TOKEN"] = token
        # Перезагружаем модуль telegram чтобы применить новый токен
        import importlib
        from app import telegram
        importlib.reload(telegram)
    return redirect(url_for("settings"))

@app.route("/api/settings/timezone", methods=["POST"])
def save_timezone():
    """Сохранение часового пояса"""
    tz = request.form.get("timezone", "Asia/Yekaterinburg")
    app_settings['timezone'] = tz
    os.environ["TZ"] = tz
    time.tzset()  # Применяем часовой пояс (работает на Unix)
    return redirect(url_for("settings"))

@app.route("/api/settings/media_path", methods=["POST"])
def save_media_path():
    """Сохранение пути к медиафайлам"""
    path = request.form.get("media_path", "/app/media")
    app_settings['media_path'] = path
    os.environ["MEDIA_PATH"] = path
    return redirect(url_for("settings"))

@app.route("/api/add_channel", methods=["POST"])
def add_channel():
    """Добавление канала с валидацией chat_id через Telegram API"""
    from app.telegram import send_text
    
    chat_id = request.form["chat_id"]
    name = request.form["name"]
    
    # Проверяем, существует ли канал и добавлен ли бот
    success, err = send_text(chat_id, "🔍 Проверка подключения к каналу...")
    if not success:
        return jsonify({
            "success": False, 
            "error": f"Не удалось подключиться к каналу: {err}. Убедитесь, что бот добавлен в канал как администратор."
        }), 400
    
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO channels (chat_id, name) VALUES (?,?)", 
                    (chat_id, name))
        conn.commit()
    return redirect(url_for("dashboard"))

@app.route("/api/edit_channel/<int:cid>", methods=["POST"])
def edit_channel(cid):
    """Редактирование канала: имя и chat_id"""
    name = request.form["name"]
    chat_id = request.form["chat_id"]
    
    with get_conn() as conn:
        # Проверяем, не используется ли этот chat_id другим каналом
        existing = conn.execute("SELECT id FROM channels WHERE chat_id=? AND id!=?", (chat_id, cid)).fetchone()
        if existing:
            return jsonify({"success": False, "error": "Такой Chat ID уже используется другим каналом"}), 400
        
        conn.execute("UPDATE channels SET name=?, chat_id=? WHERE id=?", (name, chat_id, cid))
        conn.commit()
    # Возвращаем JSON вместо redirect для корректной обработки ошибки в JS
    return jsonify({"success": True, "redirect": request.referrer or url_for("queues_list")})

@app.route("/api/delete_channel/<int:cid>", methods=["POST"])
def delete_channel(cid):
    """Удаление канала"""
    with get_conn() as conn:
        # Проверяем, есть ли очереди у этого канала
        queues_count = conn.execute("SELECT COUNT(*) FROM queues WHERE channel_id=?", (cid,)).fetchone()[0]
        if queues_count > 0:
            return jsonify({"success": False, "error": f"Нельзя удалить канал: у него есть {queues_count} очередей"}), 400
        conn.execute("DELETE FROM channels WHERE id=?", (cid,))
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/queues")
def queues_list():
    with get_conn() as conn:
        # Получаем все данные ВНУТРИ блока with
        queues_raw = conn.execute("""
            SELECT q.*, c.name as channel_name, c.chat_id 
            FROM queues q LEFT JOIN channels c ON q.channel_id = c.id
            ORDER BY c.id, q.queue_order, q.start_time
        """).fetchall()
        
        channels_raw = conn.execute("SELECT * FROM channels").fetchall()
        
        # Считаем отправленные файлы для всех очередей одним запросом
        sent_counts = {
            row["queue_id"]: row["cnt"]
            for row in conn.execute("""
                SELECT queue_id, COUNT(*) as cnt 
                FROM post_log 
                WHERE status='sent' 
                GROUP BY queue_id
            """).fetchall()
        }
    
    # Конвертируем в словари
    queues = [dict(row) for row in queues_raw]
    channels = [dict(row) for row in channels_raw]
    
    # Добавляем поля вне БД
    now = datetime.datetime.now()
    for q in queues:
        files = get_files_from_queue(q["source_path"])
        q["total"] = len(files)  # Текущее количество файлов в папке
        q["sent"] = sent_counts.get(q["id"], 0)  # Сколько уже отправлено
        
        # ETA - рассчитываем от фактического времени старта (actual_start_time) или от текущего времени
        rem = max(0, q["total"] - q["sent"])
        if rem > 0 and q["interval_sec"]:
            # Если есть actual_start_time, используем его для расчёта ETA
            if q.get("actual_start_time") and q["actual_start_time"] != 'None':
                try:
                    actual_start = datetime.datetime.strptime(q["actual_start_time"], "%Y-%m-%d %H:%M:%S")
                    min_sec = rem * q["interval_sec"]
                    max_sec = rem * (q["interval_sec"] + q["jitter_sec"])
                    eta_min_dt = actual_start + datetime.timedelta(seconds=min_sec)
                    eta_max_dt = actual_start + datetime.timedelta(seconds=max_sec)
                    q["eta_min"] = eta_min_dt.strftime("%d.%m %H:%M")
                    q["eta_max"] = eta_max_dt.strftime("%d.%m %H:%M")
                except:
                    # Фолбэк на старый метод при ошибке парсинга
                    min_sec = rem * q["interval_sec"]
                    max_sec = rem * (q["interval_sec"] + q["jitter_sec"])
                    q["eta_min"] = (now + datetime.timedelta(seconds=min_sec)).strftime("%d.%m %H:%M")
                    q["eta_max"] = (now + datetime.timedelta(seconds=max_sec)).strftime("%d.%m %H:%M")
            else:
                # Старый метод - от текущего времени
                min_sec = rem * q["interval_sec"]
                max_sec = rem * (q["interval_sec"] + q["jitter_sec"])
                q["eta_min"] = (now + datetime.timedelta(seconds=min_sec)).strftime("%d.%m %H:%M")
                q["eta_max"] = (now + datetime.timedelta(seconds=max_sec)).strftime("%d.%m %H:%M")
        else:
            q["eta_min"] = q["eta_max"] = "—"
    
    folders = get_folders_list()
    return render_template("queues.html", queues=queues, channels=channels, 
                          folders=folders, folders_json=json.dumps(folders))

@app.route("/queue/<int:qid>/manage")
def manage_queue(qid):
    with get_conn() as conn:
        queue = conn.execute("SELECT * FROM queues WHERE id=?", (qid,)).fetchone()
        if not queue: return "Очередь не найдена", 404
        channel = conn.execute("SELECT * FROM channels WHERE id=?", (queue["channel_id"],)).fetchone()
        if not channel: return "Канал не найден", 404
        logs = conn.execute("SELECT * FROM post_log WHERE queue_id=? ORDER BY sent_at DESC LIMIT 50", (qid,)).fetchall()
        # Считаем количество отправленных файлов
        sent_count = conn.execute("SELECT COUNT(*) as cnt FROM post_log WHERE queue_id=? AND status='sent'", (qid,)).fetchone()["cnt"]
    # Преобразуем rowfactory в dict и добавляем sent_count
    queue_dict = dict(queue)
    queue_dict["last_index"] = sent_count
    return render_template("queue_manage.html", queue=queue_dict, channel=channel, logs=logs)

@app.route("/api/add_queue", methods=["POST"])
def add_queue():
    start = request.form.get("start") or None
    end = request.form.get("end") or None
    
    # Если время не указано, ставим очень далёкое будущее для бесконечной очереди
    if not start and not end:
        start = "2099-01-01 00:00"
        end = "2099-12-31 23:59"
    elif not start:
        start = time.strftime("%Y-%m-%d %H:%M:%S")
    
    with get_conn() as conn:
        # Определяем статус: если время в будущем — pending, иначе queued
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        status = 'pending' if start > now else 'queued'
        
        conn.execute("""
            INSERT INTO queues (channel_id, name, start_time, end_time, interval_sec, jitter_sec, status, source_path, queue_order) 
            VALUES (?,?,?,?,?, ?,?,?, (SELECT COALESCE(MAX(queue_order),0)+1 FROM queues WHERE channel_id=?))
        """, (request.form["channel_id"], request.form["name"], start, end,
             max(10,int(request.form["interval"])), max(0,int(request.form["jitter"])), 
             status, request.form["source_path"], request.form["channel_id"]))
        conn.commit()
    return redirect(url_for("queues_list"))

@app.route("/api/queue/<int:qid>/toggle", methods=["POST"])
def toggle_queue(qid):
    with get_conn() as conn:
        st = conn.execute("SELECT status FROM queues WHERE id=?", (qid,)).fetchone()["status"]
        # Кнопка toggle показывает только для paused и active
        if st == "active":
            new_st = "paused"
        elif st == "paused":
            new_st = "queued"  # Возвращаем в очередь, а не сразу active
        else:
            return redirect(request.referrer or url_for("queues_list"))
        conn.execute("UPDATE queues SET status=?, force_active=0 WHERE id=?", (new_st, qid))
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/api/queue/<int:qid>/force", methods=["POST"])
def force_queue(qid):
    """Принудительный запуск: ставит текущую active в paused, новую делает active игнорируя время"""
    with get_conn() as conn:
        q = conn.execute("SELECT channel_id FROM queues WHERE id=?", (qid,)).fetchone()
        if not q:
            return "Очередь не найдена", 404
        
        # Находим текущую активную очередь этого канала и ставим на паузу
        current = conn.execute("SELECT id FROM queues WHERE channel_id=? AND status='active'", (q["channel_id"],)).fetchone()
        if current:
            conn.execute("UPDATE queues SET status='paused', prev_queue_id=? WHERE id=?", (qid, current["id"]))
        
        # Запускаем новую очередь немедленно
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE queues SET status='active', force_active=1, queue_order=0, actual_start_time=?, start_time=? WHERE id=?", 
                    (now, now, qid))
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/api/queue/<int:qid>/queue", methods=["POST"])
def queue_pending(qid):
    """Переводит очередь из pending в queued"""
    with get_conn() as conn:
        # Получаем данные очереди
        q = conn.execute("SELECT channel_id, queue_order FROM queues WHERE id=?", (qid,)).fetchone()
        if not q:
            return "Очередь не найдена", 404
        
        # Вычисляем следующий порядок в очереди
        max_order = conn.execute("""
            SELECT COALESCE(MAX(queue_order), 0) + 1 
            FROM queues 
            WHERE channel_id=? AND status IN ('queued', 'active')
        """, (q["channel_id"],)).fetchone()[0]
        
        # Обновляем статус и порядок
        conn.execute("UPDATE queues SET status='queued', queue_order=? WHERE id=?", (max_order, qid))
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/api/queue/<int:qid>/delete", methods=["POST"])
def delete_queue(qid):
    with get_conn() as conn:
        # Сначала проверяем статус очереди
        q = conn.execute("SELECT status FROM queues WHERE id=?", (qid,)).fetchone()
        if not q:
            return redirect(request.referrer or url_for("queues_list"))
        
        # Если очередь активна или на паузе, нужно корректно продвинуть следующую
        if q["status"] in ("active", "paused"):
            ch = conn.execute("SELECT channel_id FROM queues WHERE id=?", (qid,)).fetchone()
            if ch:
                # Помечаем как ended для триггера auto_switch
                conn.execute("UPDATE queues SET status='ended' WHERE id=?", (qid,))
        
        # Обнуляем prev_queue_id у всех очередей, которые ссылаются на удаляемую
        conn.execute("UPDATE queues SET prev_queue_id=0 WHERE prev_queue_id=?", (qid,))
        
        conn.execute("DELETE FROM post_log WHERE queue_id=?", (qid,))
        conn.execute("DELETE FROM queues WHERE id=?", (qid,))
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/api/test_send/<int:qid>", methods=["POST"])
def test_send(qid):
    from app.telegram import send_text
    with get_conn() as conn:
        queue = conn.execute("SELECT * FROM queues WHERE id=?", (qid,)).fetchone()
        channel = conn.execute("SELECT chat_id, name FROM channels WHERE id=?", (queue["channel_id"],)).fetchone()
        success, err = send_text(channel["chat_id"], f"🧪 Тест: {queue['name']}")
        # НЕ логируем тестовые сообщения в post_log, чтобы не портить счётчик
        conn.commit()
    return jsonify({"success": success, "error": err})

@app.route("/api/queue/<int:qid>/move", methods=["POST"])
def move_queue(qid):
    """Изменяет порядок очереди (повышает/понижает приоритет)"""
    direction = request.form.get("direction")  # "up" или "down"
    
    with get_conn() as conn:
        q = conn.execute("SELECT channel_id, queue_order FROM queues WHERE id=?", (qid,)).fetchone()
        if not q or q["queue_order"] == 0:
            return redirect(request.referrer or url_for("queues_list"))
        
        channel_id = q["channel_id"]
        current_order = q["queue_order"]
        
        if direction == "up" and current_order > 1:
            new_order = current_order - 1
            # Меняем местами с предыдущей очередью
            prev_q = conn.execute("""
                SELECT id FROM queues 
                WHERE channel_id=? AND queue_order=? AND status IN ('queued', 'active')
            """, (channel_id, new_order)).fetchone()
            if prev_q:
                conn.execute("UPDATE queues SET queue_order=-1 WHERE id=?", (prev_q["id"],))
                conn.execute("UPDATE queues SET queue_order=? WHERE id=?", (new_order, qid))
                conn.execute("UPDATE queues SET queue_order=? WHERE id=?", (current_order, prev_q["id"]))
        elif direction == "down":
            new_order = current_order + 1
            # Меняем местами со следующей очередью
            next_q = conn.execute("""
                SELECT id FROM queues 
                WHERE channel_id=? AND queue_order=? AND status IN ('queued', 'active')
            """, (channel_id, new_order)).fetchone()
            if next_q:
                conn.execute("UPDATE queues SET queue_order=-1 WHERE id=?", (next_q["id"],))
                conn.execute("UPDATE queues SET queue_order=? WHERE id=?", (new_order, qid))
                conn.execute("UPDATE queues SET queue_order=? WHERE id=?", (current_order, next_q["id"]))
        
        conn.commit()
    return redirect(request.referrer or url_for("queues_list"))

@app.route("/health")
def health_check():
    """Health-check endpoint для мониторинга"""
    return jsonify({"status": "ok", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})

from app.telegram import send_text, send_media
start_scheduler()

if __name__ == "__main__":
    print(f"🚀 TG Poster: http://192.168.0.103:7777")
    app.run(host="0.0.0.0", port=7777, debug=False)
