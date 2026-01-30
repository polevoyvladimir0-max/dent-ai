# syntax=docker/dockerfile:1.8
ARG BASE_IMAGE=dent_ai_base:dev
FROM ${BASE_IMAGE}

COPY . /app

ENV TELEGRAM_BOT_TOKEN="" \
    PRICING_API_BASE="" \
    REDIS_HOST="" \
    REDIS_PORT="" \
    REDIS_PASSWORD="" \
    OTEL_EXPORTER_OTLP_ENDPOINT=""

CMD ["python", "-m", "bot.main"]
