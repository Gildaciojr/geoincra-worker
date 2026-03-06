FROM python:3.11-slim

WORKDIR /app

# =========================================================
# Dependências de sistema (Playwright + OCR)
# =========================================================
RUN apt-get update && apt-get install -y \
    wget \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libxss1 \
    libasound2 \
    libgbm1 \
    libxshmfence1 \
    libu2f-udev \
    fonts-liberation \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# =========================================================
# Dependências Python
# =========================================================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =========================================================
# Google Vision credentials
# =========================================================
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/google-vision.json
COPY google-vision.json /app/google-vision.json

# =========================================================
# Playwright browser
# =========================================================
RUN playwright install chromium

# =========================================================
# Código da aplicação
# =========================================================
COPY app ./app

# =========================================================
# PYTHONPATH
# =========================================================
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# =========================================================
# Inicialização
# =========================================================
CMD ["python", "app/main.py"]