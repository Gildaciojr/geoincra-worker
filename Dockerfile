FROM python:3.11-slim

WORKDIR /app

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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV GOOGLE_APPLICATION_CREDENTIALS=/app/google-vision.json
COPY google-vision.json /app/google-vision.json

RUN playwright install chromium

COPY app ./app

ENV PYTHONPATH=/app/app:/backend
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/app/main.py"]