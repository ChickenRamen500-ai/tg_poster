FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3.10-venv python3-pip curl \
    libgl1 libglib2.0-0 libavif-dev libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Виртуальное окружение
RUN python3.10 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Обновляем pip
RUN pip install --no-cache-dir --upgrade pip

# Копируем и устанавливаем зависимости (без onnxruntime)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем onnxruntime-gpu из индекса CUDA 12 (теперь cuDNN 9 в образе есть)
RUN pip uninstall -y onnxruntime onnxruntime-gpu 2>/dev/null; \
    pip install --no-cache-dir \
    onnxruntime-gpu \
    --extra-index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/

# Копируем файлы проекта
WORKDIR /workspace
COPY run_pipeline.py .
COPY selected_tags.csv .
COPY thresholds.csv .
COPY categories.json .

# Держим контейнер живым
ENTRYPOINT ["tail", "-f", "/dev/null"]