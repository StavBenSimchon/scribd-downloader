# syntax=docker/dockerfile:1
# Scribd downloader in container. Chrome + chromedriver pinned to same version.
#
# Targets:
#   base  - python + chrome + deps (shared)
#   web   - website (FastAPI + queue + frontend)
#   cli   - command line tool (default)

FROM python:3.12-slim-bookworm AS base

# --- apt: tools + Chrome runtime libs + fonts ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
        fonts-liberation \
        fonts-noto-color-emoji \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libu2f-udev \
        libvulkan1 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# --- Chrome for Testing + matching chromedriver (both same version!) ---
ARG CHROME_VERSION=158.0.8127.36
RUN curl -fsSL "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip" -o /tmp/chrome.zip \
    && curl -fsSL "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" -o /tmp/chromedriver.zip \
    && unzip -q /tmp/chrome.zip -d /opt \
    && unzip -q /tmp/chromedriver.zip -d /opt \
    && chmod +x /opt/chrome-linux64/chrome \
                /opt/chromedriver-linux64/chromedriver \
    && ln -s /opt/chrome-linux64/chrome /usr/local/bin/chrome \
    && ln -s /opt/chrome-linux64/chrome /usr/local/bin/google-chrome \
    && ln -s /opt/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && rm /tmp/chrome.zip /tmp/chromedriver.zip

ENV CHROME_BIN=/opt/chrome-linux64/chrome \
    CHROMEDRIVER_PATH=/opt/chromedriver-linux64/chromedriver \
    SCRIBD_HEADLESS=1 \
    PYTHONUNBUFFERED=1

# --- python deps ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scribd-downloader.py .


# --- website: FastAPI + queue + frontend ---
FROM base AS web

COPY web/requirements.txt /tmp/web-requirements.txt
RUN pip install --no-cache-dir -r /tmp/web-requirements.txt

COPY web/ /app/web/

ENV SCRIBD_OUTPUT_DIR=/downloads \
    SCRIBD_SCRIPT=/app/scribd-downloader.py

WORKDIR /downloads
EXPOSE 8000

# ONE worker process = ONE queue. Never raise workers, queue lives in memory.
CMD ["uvicorn", "app:app", "--app-dir", "/app/web", "--host", "0.0.0.0", "--port", "8000"]


# --- command line tool (default target) ---
FROM base AS cli

# PDFs land here by default (mount a volume to keep them)
WORKDIR /downloads

ENTRYPOINT ["python", "/app/scribd-downloader.py"]
