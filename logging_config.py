# logging_config.py
import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging():
    """Настройка логирования с выводом в файл и консоль"""
    
    # Создаём директорию для логов
    log_dir = os.getenv("LOG_DIR", "/app/logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "poster.log")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Создаём logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Очищаем старые handlers
    logger.handlers = []
    
    # Формат логов
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler с ротацией (10MB, 5 файлов)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024, 
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logging.info(f"📝 Логирование настроено: {log_file} (уровень: {log_level})")
