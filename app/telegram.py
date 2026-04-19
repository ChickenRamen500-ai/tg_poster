# app/telegram.py
import requests
import os
import time
import random
import logging

# Настройка логирования
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_text(chat_id, text):
    url = f"{API_BASE}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=15)
        resp.raise_for_status()
        logger.info(f"✅ Текст отправлен в {chat_id}")
        return True, None
    except Exception as e:
        logger.error(f"❌ Ошибка отправки текста: {e}")
        return False, str(e)

def send_media(chat_id, file_path, caption=""):
    """Отправляет файл (фото/видео/документ) в канал"""
    if not os.path.exists(file_path):
        logger.error(f"❌ Файл не найден: {file_path}")
        return False, f"Файл не найден: {file_path}"
    
    ext = os.path.splitext(file_path)[1].lower()
    
    # Определяем метод API по расширению
    # GIF проверяем ПЕРЕД фото, чтобы отправлять как animation
    if ext == ".gif":
        method = "sendAnimation"
        file_key = "animation"
    elif ext in [".jpg", ".jpeg", ".png"]:
        method = "sendPhoto"
        file_key = "photo"
    elif ext in [".mp4", ".webm", ".mkv"]:
        method = "sendVideo"
        file_key = "video"
    elif ext in [".avif"]:
        # AVIF поддерживается Telegram как изображение
        method = "sendPhoto"
        file_key = "photo"
    else:
        method = "sendDocument"
        file_key = "document"
    
    url = f"{API_BASE}/{method}"
    
    try:
        with open(file_path, "rb") as f:
            files = {file_key: f}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "HTML"
            
            resp = requests.post(url, data=data, files=files, timeout=60)
            resp.raise_for_status()
            logger.info(f"✅ Файл {file_path} отправлен как {method}")
            return True, None
    except Exception as e:
        logger.error(f"❌ Ошибка отправки файла {file_path}: {e}")
        return False, str(e)

def get_next_delay(interval, jitter):
    """Расчитывает задержку с учётом jitter, защищая от отрицательных значений"""
    delay = interval + random.randint(-jitter, jitter)
    return max(0, delay)
