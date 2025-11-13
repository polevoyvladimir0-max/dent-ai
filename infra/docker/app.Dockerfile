# syntax=docker/dockerfile:1.8
FROM python:3.13-slim@sha256:4f26ee9425c6999a2adc111653d62aed1989af766ad0e0db7a07fe521385e1ac

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update \ 
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
        git \
        cmake \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt /app/requirements-runtime.txt
RUN pip install --upgrade pip \ 
    && pip install -r /app/requirements-runtime.txt

COPY . /app

RUN chmod +x infra/docker/app-entrypoint.sh

ENV OTEL_EXPORTER_OTLP_ENDPOINT="" \
    QDRANT_URL="" \
    MINIO_ENDPOINT="" \
    MINIO_ROOT_USER="" \
    MINIO_ROOT_PASSWORD="" \
    POSTGRES_USER="" \
    POSTGRES_PASSWORD="" \
    POSTGRES_DB="" \
    POSTGRES_HOST="" \
    POSTGRES_PORT="" \
    REDIS_HOST="" \
    REDIS_PORT="" \
    REDIS_PASSWORD="" \
    TELEGRAM_BOT_TOKEN="" \
    PRICING_API_BASE=""

EXPOSE 8000

ENTRYPOINT ["./infra/docker/app-entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
