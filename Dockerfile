FROM python:3.11-slim

# Install system deps required by Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libwayland-client0 fonts-liberation git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY src/ src/

RUN pip install --no-cache-dir -e . \
    && playwright install chromium

# Output directory for screenshots (writable by any user for HF Spaces)
RUN mkdir -p /app/output && chmod 777 /app/output

ENV DOCKER_ENV=1
ENV PYTHONUNBUFFERED=1

# HuggingFace Spaces uses port 7860 by default
EXPOSE 7860

CMD ["portal-fetcher", "serve", "--host", "0.0.0.0", "--port", "7860"]
