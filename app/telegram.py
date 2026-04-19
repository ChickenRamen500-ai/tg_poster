# app/telegram.py
import requests
import os
import time
import random
import logging
from pathlib import Path

# Настройка логирования
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен в переменных окружения!")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Лимиты Telegram
PHOTO_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
DOCUMENT_MAX_SIZE = 50 * 1024 * 1024  # 50 MB
IMAGE_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2 MB - порог для сжатия изображений

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

def get_file_size(file_path):
    """Возвращает размер файла в байтах"""
    return os.path.getsize(file_path)

def send_image_as_document(chat_id, file_path, caption=""):
    """Отправляет изображение как документ (без сжатия)"""
    url = f"{API_BASE}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "HTML"
            
            resp = requests.post(url, data=data, files=files, timeout=60)
            resp.raise_for_status()
            logger.info(f"✅ Файл {file_path} отправлен как документ")
            return True, None
    except Exception as e:
        logger.error(f"❌ Ошибка отправки документа {file_path}: {e}")
        return False, str(e)

def send_media(chat_id, file_path, caption=""):
    """
    Отправляет файл (фото/видео/документ) в канал.
    
    Для изображений >2MB: сначала отправляет сжатое фото, затем (через паузу) оригинал как документ.
    Возвращает кортеж (success, error, sent_as_document) где sent_as_document=True если была двойная отправка.
    """
    if not os.path.exists(file_path):
        logger.error(f"❌ Файл не найден: {file_path}")
        return False, f"Файл не найден: {file_path}", False
    
    ext = os.path.splitext(file_path)[1].lower()
    file_size = get_file_size(file_path)
    
    # Проверка лимитов
    if ext in [".jpg", ".jpeg", ".png", ".avif"] and file_size > PHOTO_MAX_SIZE:
        logger.warning(f"⚠️ Фото превышает лимит 10MB ({file_size} байт). Отправляем как документ.")
        return send_image_as_document(chat_id, file_path, caption)
    
    if file_size > DOCUMENT_MAX_SIZE:
        logger.error(f"❌ Файл превышает лимит 50MB ({file_size} байт)")
        return False, f"Файл превышает лимит 50MB ({file_size} байт)", False
    
    # Особое поведение для изображений >2MB: сначала сжатое фото, затем документ
    if ext in [".jpg", ".jpeg", ".png"] and file_size > IMAGE_COMPRESS_THRESHOLD:
        logger.info(f"📸 Изображение >2MB ({file_size} байт). Отправляем сжатое фото + документ.")
        
        # Шаг 1: Отправляем сжатое фото
        photo_url = f"{API_BASE}/sendPhoto"
        try:
            with open(file_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "HTML"
                
                resp = requests.post(photo_url, data=data, files=files, timeout=60)
                resp.raise_for_status()
                logger.info(f"✅ Сжатое фото отправлено")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сжатого фото: {e}")
            return False, str(e), False
        
        # Шаг 2: Пауза 15-20 секунд перед отправкой документа
        delay = random.randint(15, 20)
        logger.info(f"⏱ Пауза {delay} сек перед отправкой документа...")
        time.sleep(delay)
        
        # Шаг 3: Отправляем как документ (ОТВЕТНЫМ сообщением будет handled outside)
        return send_image_as_document(chat_id, file_path, caption=caption + " (оригинал)" if caption else "(оригинал)")
    
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
            return True, None, (ext in [".jpg", ".jpeg", ".png"] and file_size > IMAGE_COMPRESS_THRESHOLD)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки файла {file_path}: {e}")
        return False, str(e), False

def get_next_delay(interval, jitter):
    """Расчитывает задержку с учётом jitter, защищая от отрицательных значений"""
    delay = interval + random.randint(-jitter, jitter)
    return max(0, delay)
