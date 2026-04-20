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

# Поддерживаемые форматы изображений для sendPhoto
SUPPORTED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Максимальное количество повторных попыток при ошибке 429
MAX_RETRIES = 3
BASE_RETRY_DELAY = 5  # Базовая задержка между попытками (секунды)

def get_bot_id():
    """Получает ID бота из токена"""
    if BOT_TOKEN:
        return BOT_TOKEN.split(':')[0]
    return None

def send_notification_to_users(message):
    """Отправляет уведомление всем разрешённым пользователям"""
    from app.db import get_allowed_users
    
    allowed_users = get_allowed_users()
    if not allowed_users:
        logger.debug("ℹ️ Нет разрешённых пользователей для уведомлений")
        return False
    
    success_count = 0
    for user in allowed_users:
        user_id = user.get('user_id') if isinstance(user, dict) else user
        try:
            result = send_text(user_id, message)
            if result[0]:
                success_count += 1
                logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
            else:
                logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user_id}: {result[1]}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")
    
    return success_count > 0

def validate_chat(chat_id):
    """
    Проверяет существование чата/канала через getChat API.
    Возвращает кортеж (success, error_message).
    """
    url = f"{API_BASE}/getChat"
    try:
        resp = requests.post(url, json={"chat_id": chat_id}, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok"):
            chat_info = result.get("result", {})
            chat_title = chat_info.get("title", chat_info.get("username", "Unknown"))
            logger.info(f"✅ Чат найден: {chat_title} ({chat_id})")
            return True, None
        else:
            error = result.get("description", "Неизвестная ошибка")
            logger.error(f"❌ Ошибка проверки чата: {error}")
            return False, error
    except requests.exceptions.HTTPError as e:
        error_msg = str(e)
        if "404" in error_msg:
            return False, "Чат не найден. Проверьте chat_id."
        elif "400" in error_msg:
            return False, "Неверный формат chat_id."
        else:
            return False, f"HTTP ошибка: {e}"
    except requests.exceptions.Timeout as e:
        logger.error(f"❌ Ошибка проверки чата: таймаут подключения")
        return False, "Таймаут подключения к Telegram API. Проверьте интернет-соединение."
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Ошибка проверки чата: ошибка подключения")
        return False, "Не удалось подключиться к Telegram API. Проверьте интернет-соединение."
    except Exception as e:
        logger.error(f"❌ Ошибка проверки чата: {e}")
        return False, f"Ошибка подключения: {type(e).__name__}"

def handle_rate_limit(response_json):
    """
    Обрабатывает ошибку 429 Too Many Requests.
    Возвращает количество секунд для ожидания, если нужно ждать.
    """
    if response_json.get("error_code") == 429:
        retry_after = response_json.get("parameters", {}).get("retry_after", BASE_RETRY_DELAY)
        logger.warning(f"⚠️ Rate limit! Ждём {retry_after} секунд...")
        return retry_after
    return None

def send_text(chat_id, text):
    url = f"{API_BASE}/sendMessage"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            }, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            
            # Проверяем на rate limit
            if not result.get("ok") and result.get("error_code") == 429:
                retry_after = handle_rate_limit(result)
                if retry_after and attempt < MAX_RETRIES - 1:
                    time.sleep(retry_after)
                    continue
                else:
                    logger.error(f"❌ Превышен лимит запросов после {MAX_RETRIES} попыток")
                    return False, "Rate limit exceeded"
            
            logger.info(f"✅ Текст отправлен в {chat_id}")
            return True, None
        except requests.exceptions.HTTPError as e:
            # Пробуем распарсить ответ для проверки на 429
            try:
                result = e.response.json()
                if result.get("error_code") == 429:
                    retry_after = handle_rate_limit(result)
                    if retry_after and attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
            except:
                pass
            logger.error(f"❌ Ошибка отправки текста (попытка {attempt+1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return False, str(e)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки текста: {e}")
            return False, str(e)
    
    return False, "Max retries exceeded"

def get_file_size(file_path):
    """Возвращает размер файла в байтах"""
    return os.path.getsize(file_path)

def send_image_as_document(chat_id, file_path, caption=""):
    """Отправляет изображение как документ (без сжатия) с retry logic"""
    url = f"{API_BASE}/sendDocument"
    for attempt in range(MAX_RETRIES):
        try:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "HTML"
                
                resp = requests.post(url, data=data, files=files, timeout=60)
                resp.raise_for_status()
                result = resp.json()
                
                # Проверяем на rate limit
                if not result.get("ok") and result.get("error_code") == 429:
                    retry_after = handle_rate_limit(result)
                    if retry_after and attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
                
                logger.info(f"✅ Файл {file_path} отправлен как документ")
                return True, None
        except requests.exceptions.HTTPError as e:
            try:
                result = e.response.json()
                if result.get("error_code") == 429:
                    retry_after = handle_rate_limit(result)
                    if retry_after and attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
            except:
                pass
            logger.error(f"❌ Ошибка отправки документа (попытка {attempt+1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return False, str(e)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки документа {file_path}: {e}")
            return False, str(e)
    
    return False, "Max retries exceeded"

def send_media(chat_id, file_path, caption=""):
    """
    Отправляет файл (фото/видео/аудио/документ) в канал.
    
    Для изображений >2MB: сначала отправляет сжатое фото, затем (через паузу) оригинал как документ.
    Возвращает кортеж (success, error, sent_as_document) где sent_as_document=True если была двойная отправка.
    """
    if not os.path.exists(file_path):
        logger.error(f"❌ Файл не найден: {file_path}")
        return False, f"Файл не найден: {file_path}", False
    
    ext = os.path.splitext(file_path)[1].lower()
    file_size = get_file_size(file_path)
    
    # Проверка лимитов
    if ext in SUPPORTED_IMAGE_FORMATS and file_size > PHOTO_MAX_SIZE:
        logger.warning(f"⚠️ Фото превышает лимит 10MB ({file_size} байт). Отправляем как документ.")
        return send_image_as_document(chat_id, file_path, caption)
    
    if file_size > DOCUMENT_MAX_SIZE:
        logger.error(f"❌ Файл превышает лимит 50MB ({file_size} байт)")
        return False, f"Файл превышает лимит 50MB ({file_size} байт)", False
    
    # Особое поведение для изображений >2MB: сначала сжатое фото, затем документ
    if ext in [".jpg", ".jpeg", ".png"] and file_size > IMAGE_COMPRESS_THRESHOLD:
        logger.info(f"📸 Изображение >2MB ({file_size} байт). Отправляем сжатое фото + документ.")
        
        # Шаг 1: Отправляем сжатое фото с retry logic
        photo_url = f"{API_BASE}/sendPhoto"
        photo_success = False
        for attempt in range(MAX_RETRIES):
            try:
                with open(file_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": chat_id}
                    if caption:
                        data["caption"] = caption
                        data["parse_mode"] = "HTML"
                    
                    resp = requests.post(photo_url, data=data, files=files, timeout=120)
                    resp.raise_for_status()
                    result = resp.json()
                    
                    # Проверяем на rate limit
                    if not result.get("ok") and result.get("error_code") == 429:
                        retry_after = handle_rate_limit(result)
                        if retry_after and attempt < MAX_RETRIES - 1:
                            time.sleep(retry_after)
                            continue
                    
                    logger.info(f"✅ Сжатое фото отправлено")
                    photo_success = True
                    break
            except requests.exceptions.HTTPError as e:
                try:
                    result = e.response.json()
                    if result.get("error_code") == 429:
                        retry_after = handle_rate_limit(result)
                        if retry_after and attempt < MAX_RETRIES - 1:
                            time.sleep(retry_after)
                            continue
                except:
                    pass
                logger.error(f"❌ Ошибка отправки сжатого фото (попытка {attempt+1}): {e}")
                if attempt == MAX_RETRIES - 1:
                    return False, str(e), False
            except Exception as e:
                logger.error(f"❌ Ошибка отправки сжатого фото: {e}")
                return False, str(e), False
        
        if not photo_success:
            return False, "Failed to send compressed photo after retries", False
        
        # Шаг 2: Пауза 15-20 секунд перед отправкой документа
        delay = random.randint(15, 20)
        logger.info(f"⏱ Пауза {delay} сек перед отправкой документа...")
        time.sleep(delay)
        
        # Шаг 3: Отправляем как документ (с retry logic внутри send_image_as_document)
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
    elif ext in [".mp3"]:
        method = "sendAudio"
        file_key = "audio"
    elif ext in [".wav", ".ogg", ".flac"]:
        # FLAC, WAV, OGG отправляем как документ (Telegram не поддерживает их в sendAudio)
        logger.info(f"🎵 Аудиофайл {ext} будет отправлен как документ")
        return send_image_as_document(chat_id, file_path, caption)
    elif ext == ".avif":
        # AVIF НЕ поддерживается Telegram напрямую - отправляем как документ
        logger.info(f"📎 AVIF файл будет отправлен как документ (Telegram не поддерживает AVIF в sendPhoto)")
        return send_image_as_document(chat_id, file_path, caption)
    else:
        method = "sendDocument"
        file_key = "document"
    
    url = f"{API_BASE}/{method}"
    
    # Увеличенный таймаут для больших файлов (особенно FLAC, MP3)
    request_timeout = 120 if file_size > 5 * 1024 * 1024 else 60
    
    # Отправка файла с retry logic
    for attempt in range(MAX_RETRIES):
        try:
            with open(file_path, "rb") as f:
                files = {file_key: f}
                data = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "HTML"
                
                resp = requests.post(url, data=data, files=files, timeout=request_timeout)
                resp.raise_for_status()
                result = resp.json()
                
                # Проверяем на rate limit
                if not result.get("ok") and result.get("error_code") == 429:
                    retry_after = handle_rate_limit(result)
                    if retry_after and attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
                
                logger.info(f"✅ Файл {file_path} отправлен как {method}")
                return True, None, (ext in [".jpg", ".jpeg", ".png"] and file_size > IMAGE_COMPRESS_THRESHOLD)
        except requests.exceptions.Timeout as e:
            logger.error(f"⏱ Таймаут отправки файла (попытка {attempt+1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return False, f"Timeout: {str(e)}", False
            time.sleep(5)  # Пауза перед повторной попыткой
        except requests.exceptions.HTTPError as e:
            try:
                result = e.response.json()
                if result.get("error_code") == 429:
                    retry_after = handle_rate_limit(result)
                    if retry_after and attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
            except:
                pass
            logger.error(f"❌ Ошибка отправки файла (попытка {attempt+1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return False, str(e), False
        except Exception as e:
            logger.error(f"❌ Ошибка отправки файла {file_path}: {e}")
            return False, str(e), False
    
    return False, "Max retries exceeded", False

def get_next_delay(interval, jitter):
    """Расчитывает задержку с учётом jitter, защищая от отрицательных значений"""
    delay = interval + random.randint(-jitter, jitter)
    return max(0, delay)
