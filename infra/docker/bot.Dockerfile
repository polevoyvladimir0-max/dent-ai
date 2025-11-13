# syntax=docker/dockerfile:1.8
FROM python:3.13-slim@sha256:4f26ee9425c6999a2adc111653d62aed1989af766ad0e0db7a07fe521385e1ac

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_PREFER_BINARY=1 \
    PIP_INDEX_URL=https://download.pytorch.org/whl/cpu \
    PIP_EXTRA_INDEX_URL=https://pypi.org/simple \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
        cmake \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt /app/requirements-runtime.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefer-binary -r /app/requirements-runtime.txt \
    && pip cache purge || true

COPY . /app

ENV TELEGRAM_BOT_TOKEN="" \
    PRICING_API_BASE="" \
    REDIS_HOST="" \
    REDIS_PORT="" \
    REDIS_PASSWORD="" \
    OTEL_EXPORTER_OTLP_ENDPOINT=""

CMD ["python", "-m", "bot.main"]
