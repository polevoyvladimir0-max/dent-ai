# syntax=docker/dockerfile:1.8
FROM alpine:3.20

RUN apk add --no-cache \
        postgresql15-client \
        curl \
        bash \
        ca-certificates \
        tzdata \
        mc \
        supercronic

WORKDIR /app

COPY infra/docker/backup/backup.sh /app/backup.sh
COPY infra/docker/backup/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/backup.sh /app/entrypoint.sh

ENV BACKUP_SCHEDULE="0 3 * * *" \
    BACKUP_RETENTION_DAYS="7" \
    BACKUP_BUCKET="dent-ai-backups"

ENTRYPOINT ["/app/entrypoint.sh"]

