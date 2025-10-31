#!/usr/bin/env sh
set -eu

SCHEDULE="${BACKUP_SCHEDULE:-0 3 * * *}"
LOG_FILE="${BACKUP_LOG_PATH:-/var/log/backup.log}"

mkdir -p "$(dirname "${LOG_FILE}")"
touch "${LOG_FILE}"

cat <<CRON >/tmp/crontab
${SCHEDULE} /app/backup.sh >> ${LOG_FILE} 2>&1
CRON

echo "[backup-entrypoint] starting supercronic with schedule: ${SCHEDULE}"
exec supercronic /tmp/crontab

