# =====================================================================
# Google Photos to Telegram Migration Engine Dockerfile
# =====================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies (build-essential and libjpeg/zlib for Pillow if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY main.py .

# Create volume directories
RUN mkdir -p /app/data /app/data/temp

VOLUME ["/app/data"]

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]

