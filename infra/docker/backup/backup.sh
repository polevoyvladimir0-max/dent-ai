#!/usr/bin/env bash
set -euo pipefail

timestamp="$(date -u +%Y%m%d_%H%M%S)"
backup_dir="/backups"
mkdir -p "${backup_dir}"

pg_uri="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB}"
dump_file="${backup_dir}/postgres_${POSTGRES_DB}_${timestamp}.sql.gz"

echo "[backup] starting pg_dump to ${dump_file}"
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump --no-owner --no-privileges --format=plain --dbname="${pg_uri}" | gzip > "${dump_file}"

target_bucket="${BACKUP_BUCKET:-dent-ai-backups}"
if [[ -n "${MINIO_ENDPOINT:-}" ]]; then
  echo "[backup] configuring mc alias"
  mc alias set dentai "${MINIO_ENDPOINT}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}"
  echo "[backup] ensuring bucket ${target_bucket}"
  mc mb --ignore-existing "dentai/${target_bucket}"

  echo "[backup] uploading ${dump_file}"
  mc cp "${dump_file}" "dentai/${target_bucket}/"

  retention_days="${BACKUP_RETENTION_DAYS:-7}"
  echo "[backup] pruning backups older than ${retention_days} days"
  mc rm --force --older-than "${retention_days}d" "dentai/${target_bucket}/"
else
  echo "[backup] MINIO_ENDPOINT not set, skipping upload"
fi

echo "[backup] cleanup local dump"
rm -f "${dump_file}"

echo "[backup] completed"

