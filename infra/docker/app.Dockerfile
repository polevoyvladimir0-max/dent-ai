# syntax=docker/dockerfile:1.8
ARG BASE_IMAGE=cr.yandex/crprbk2jtq9a96om7uvp/dent-ai/base:latest
FROM ${BASE_IMAGE}

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
