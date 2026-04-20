FROM python:3.13-slim

# Установка системных библиотек для поддержки AVIF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libavif-dev \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всего проекта
COPY . .

# Добавляем /app в PYTHONPATH, чтобы импорты вида "from app.xxx" работали
ENV PYTHONPATH=/app

# Переменные окружения (можно переопределить в docker-compose)
ENV TZ=Asia/Yekaterinburg

# Запуск через -m, чтобы Python правильно разрешал импорты пакетов
CMD ["python", "-m", "app.main"]
