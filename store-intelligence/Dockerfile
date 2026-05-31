# ---- Stage 1: build dependencies ----------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# System libs needed by OpenCV and torch
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch first (much smaller than default CUDA build)
RUN pip install --no-cache-dir \
    torch==2.2.1 torchvision==0.17.1 \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download YOLOv8n weights so the container can work offline
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" 2>&1 | tail -5


# ---- Stage 2: runtime image ----------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy cached YOLO weights
COPY --from=builder /root/.config /root/.config

# Copy application source
COPY . .

# Ensure data directory exists
RUN mkdir -p /app/data

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DEMO_MODE=true \
    DATA_DIR=/app/data

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
