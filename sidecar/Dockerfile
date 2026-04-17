FROM python:3.12-slim

# System dependencies for Playwright's Chromium headless shell
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2t64 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser (cached in PLAYWRIGHT_BROWSERS_PATH).
ENV PLAYWRIGHT_BROWSERS_PATH=/app/browsers
RUN playwright install chromium

RUN mkdir -p /app/bridges

COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
