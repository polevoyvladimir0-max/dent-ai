# Dent AI Runbook

## 1. Контакты и область
- **Среда**: Yandex Cloud, VM `ubuntu@158.160.203.51`, каталог `/srv/dent_ai`.
- **Доступ**: SSH-ключ `yc-dent-ai`, alias `dent-ai-vm`.
- **Назначение**: FastAPI + Telegram-бот + ML пайплайн, state в PostgreSQL/Qdrant/Redis, файлы в MinIO.

## 2. Старт/останов
- Запуск стека: `docker compose --profile observability up -d` (из `/srv/dent_ai`).
- Остановка: `docker compose down` (по необходимости `--volumes`/`--remove-orphans` осторожно).
- Перезапуск конкретного сервиса: `docker compose restart <service>`.

## 3. Состояние и мониторинг
- Сводка: `docker compose ps`.
- Логи: `docker compose logs -f <service>` либо `docker logs --tail 200 <container>`.
- Приложение: `curl http://127.0.0.1:8000/ping`.
- Grafana: `http://<публичный-IP>:3000` → Dashboard “Dent AI Overview”.
- Alerts: Grafana Alerting → “Dent AI API down” (status OK).
- Prometheus targets: `http://<IP>:9090/targets`.
- Blackbox: Probes описаны в `infra/blackbox/config.yml`.

## 4. Vault
### Инициализация (после чистого старта)
```bash
docker compose exec vault sh -c 'vault operator init -key-shares=1 -key-threshold=1'
```
Сохраняем **Unseal Key** и **Initial Root Token** в защищённый секрет-менеджер.

### Unseal (после перезагрузки)
```bash
docker compose exec vault sh -c 'vault operator unseal <UNSEAL_KEY>'
```

### Проверка состояний
```bash
docker compose exec vault sh -c 'vault status'
```

### Ротация токена
- Логин: `docker compose exec vault sh -c 'vault login <ROOT_TOKEN>'`.
- Завершить сессию: `vault token revoke -self`.

## 5. MinIO
- Консоль: `http://<IP>:9001`.
- Healthcheck: `curl -f http://127.0.0.1:9000/minio/health/ready`.
- Конфигурация бакетов через `mc` внутри контейнера:
  ```bash
  docker compose exec minio sh -c '
    mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
    mc ls local
  '
  ```

## 6. Бэкапы
- Автоматические: контейнер `postgres-backup` (cron `0 3 * * *`), складывает в MinIO бакет `dent-ai-backups`.
- Проверка статуса:
  ```bash
  docker logs --tail 100 dent_ai_pg_backup
  ```
- Ручной запуск:
  ```bash
  docker compose run --rm postgres-backup
  ```

## 7. Autoheal
- Логи: `docker logs --tail 50 dent_ai_autoheal`.
- Autoheal перезапускает контейнеры с лейблом `autoheal=true`, ориентируется на Docker healthcheck.

## 8. Деплой (ручной)
1. Локально обновляем код → `git add/commit/push`.
2. Убеждаемся, что Mutagen синкнул на ВМ (`mutagen sync flush dent-ai`).
3. На ВМ: `docker compose build app bot postgres-backup` (если были изменения в Dockerfile).
4. Реген `.env` при изменении секретов.
5. `docker compose --profile observability up -d`.
6. Пройти Post-deploy чеклист (см. README).

## 9. DR / восстановление
1. Поднять новую ВМ и прописать SSH-ключ.
2. Установить Docker, Mutagen, PowerShell (если требуется).
3. Получить репозиторий (`git clone` или Mutagen).
4. Восстановить `infra/env/secrets.prod.json` из секрет-менеджера.
5. `pwsh ... generate-env.ps1` → `.env`.
6. `docker compose build` (для кастомных образов) → `docker compose up -d`.
7. Vault unseal, проверить MinIO, восстановить данные при необходимости из бэкапа (`mc cp` + `psql`).

## 10. Инциденты
- **API 5xx** — проверить `docker logs dent_ai_app`, `postgres` подключения, Redis.
- **Бот не отвечает** — `docker logs dent_ai_bot`, убедиться в валидности `BOT_TOKEN`.
- **MinIO unhealthy** — healthcheck (`curl`), корректность `MINIO_*` из `.env`, место на диске.
- **Vault sealed** — unseal, проверить наличие ключей.
- **Prometheus/Loki/Grafana** — пересмотреть `infra/*` конфиги, `docker compose restart grafana promtail prometheus`.

## 11. Mutagen
- Статус: `mutagen sync list`.
- При конфликте файлов: Mutagen помечает `CONFLICT`. Решить вручную → `mutagen sync resolve dent-ai` (если требуется).
- Полезная команда: `mutagen monitor dent-ai` (реалтайм статус).

## 12. Хранение секретов
- Vault unseal key + root token.
- `.env` генераторные секреты (`infra/env/secrets.*.json`).
- Telegram tokens, Grafana alert tokens, MinIO/Redis/Postgres пароли.
- Хранить в YC Lockbox/Secret Manager, либо в надёжном offline-хранилище с MFA.


