FROM python:3.11-slim

WORKDIR /app

# Dependências de sistema (Playwright)
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
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Código
COPY app ./app

# 🔑 CORREÇÃO CRÍTICA
ENV PYTHONPATH=/app

CMD ["python", "app/main.py"]
