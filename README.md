# Dent AI — эксплуатация и деплой

## TL;DR
- Репозиторий ведём в Git. Все изменения фиксируем `git add/commit/push`, на сервере подтягиваем через Mutagen или `git pull`.
- Локальный `C:\dent_ai` синхронизируется с `/srv/dent_ai` через Mutagen (сессия `dent-ai`).
- `.env` генерируется PowerShell-скриптом: никакого ручного редактирования, только `generate-env.ps1`.
- Запуск стека: `docker compose --profile observability up -d`, затем проверяем `/ping`, бота, Grafana дашборд “Dent AI Overview”, алерт “Dent AI API down”, логи `postgres-backup` и `autoheal`.
- Детальный runbook лежит в `docs/runbook.md`.

## Архитектура
- `app` — FastAPI, порт 8000.
- `bot` — Telegram-bot.
- State: `postgres`, `redis`, `qdrant`, `minio`.
- Secrets: `vault`.
- Backups: `postgres-backup` (cron → MinIO).
- Observability (профиль `observability`): `prometheus`, `blackbox-exporter`, `tempo`, `loki`, `promtail`, `grafana`.
- Надзор: `autoheal` (рестартит контейнеры с лейблом `autoheal=true`).

## Инструменты
- Git ≥ 2.39.
- Docker ≥ 25 + compose plugin.
- PowerShell 7+ (`pwsh`) — генератор `.env`.
- Mutagen ≥ 0.18 на обеих сторонах (`mutagen version`).
- SSH-ключ `yc-dent-ai` прописан в `~/.ssh/config`:
  ```
  Host dent-ai-vm
      HostName 158.160.203.51
      User ubuntu
      IdentityFile C:/Users/plove/.ssh/yc-dent-ai
      IdentitiesOnly yes
      StrictHostKeyChecking accept-new
  ```

## Git + Mutagen рабочий цикл
1. `git clone git@github.com:<org>/dent_ai.git` → `cd dent_ai`.
2. Mutagen-сессия (если не создана):
   ```powershell
   mutagen sync create `
     --name dent-ai `
     --mode two-way-resolved `
     --ignore-vcs `
     --ignore '.venv' `
     --ignore '__pycache__' `
     --ignore '*.pyc' `
     --ignore 'backups' `
     --ignore 'storage' `
     --ignore 'tmp' `
     --symlink-mode portable `
     --permissions-mode manual `
     --default-file-mode 0644 `
     --default-directory-mode 0755 `
     . dent-ai-vm:/srv/dent_ai
   ```
3. Основные команды:
   - Проверка статуса: `mutagen sync list`.
   - Принудительный sync: `mutagen sync flush dent-ai`.
   - Пауза/возобновление: `mutagen sync pause dent-ai` / `mutagen sync resume dent-ai`.
   - Завершение: `mutagen sync terminate dent-ai`.

## Секреты и генерация `.env`
1. Актуальные секреты лежат в `infra/env/secrets.prod.json` (локалка — `infra/env/secrets.local.json`).
2. Генератор `.env` (PowerShell):
   ```powershell
   pwsh -NoLogo -File infra/scripts/generate-env.ps1 `
     -Target compose `
     -Profile prod `
     -SecretsPath infra/env/secrets.prod.json `
     -OutputPath .env `
     -Force
   ```
3. Для CI используем `-Target ci` и подсовываем secrets через GitHub Actions.
4. `.env` в Git не коммитим.

## Деплой / апдейт стека
```bash
cd /srv/dent_ai
docker compose --profile observability up -d
```

- При первом запуске собери свои образы: `docker compose build app bot postgres-backup`.
- Принудительно подтянуть публичные образы можно заранее (`docker pull prom/prometheus:v3.0.0` и т.д.).

## Post-deploy чеклист
1. `docker compose ps` — все сервисы `Up (healthy|running)`.
2. `curl http://127.0.0.1:8000/ping` → OK.
3. `docker logs --tail 50 dent_ai_bot` — бот без ошибок (Authorized).
4. Grafana (`http://<ip>:3000`): есть дашборд “Dent AI Overview”, алерт “Dent AI API down” в статусе OK.
5. MinIO (`http://<ip>:9001`): авторизация root user/pass, бакет `dent-ai` существует.
6. Vault: `vault status` → `Sealed: false` (если sealed — см. runbook).
7. Backups: `docker logs --tail 50 dent_ai_pg_backup` — свежий запуск.
8. Autoheal: `docker logs --tail 20 dent_ai_autoheal` — видит контейнеры и healthchecks.

## Runbook и операции
Оперативные процедуры (Vault init/unseal, disaster recovery, ручной бэкап, анализ алертов) описаны в `docs/runbook.md`.

### Быстрые команды
- Vault init: `docker compose exec vault sh -c 'vault operator init -key-shares=1 -key-threshold=1'`.
- Vault unseal: `docker compose exec vault sh -c 'vault operator unseal <UNSEAL_KEY>'`.
- Ручной бэкап: `docker compose run --rm postgres-backup`.
- Health MinIO: `curl -f http://127.0.0.1:9000/minio/health/ready`.
- Restart сервиса: `docker compose restart <service>`.

## CI/CD
- Workflow `./.github/workflows/ci.yml` выполняет тесты, линтеры, SCA, собирает multi-arch образы с SBOM и Cosign, деплоит по digest.
- Перед деплоем валидирует секреты скриптом `generate-env.ps1 -Target ci`.
- Для ручного деплоя через CI — обновляем Git, пушим, ждём зелёного пайплайна.

## Траблшутинг
- `Permission denied (publickey)` при Mutagen/SSH → проверь `~/.ssh/config` и права на ключ.
- MinIO unhealthy → убедись, что healthcheck использует `curl`, в `.env` корректные `MINIO_*`, при необходимости `docker compose logs minio`.
- Vault sealed после рестарта → `vault operator unseal`. Если token истёк — valт init не повторяем, используем сохранённые ключи.
- `docker compose` ищет `.env` в другом каталоге → обязательно `cd /srv/dent_ai`.
- `psycopg2 OperationalError` → пароль в контейнере не совпадает с `.env`; после смены пароля нужно пересоздать volume или применить `ALTER USER ... PASSWORD` внутри БД.

## Версии и контроль изменений
- Все конфигурационные правки (включая этот README, `docs/runbook.md`, `mutagen.yml`) коммитим в Git.
- Перед пушем: `git status` → `git add` нужные файлы → `git commit -m "docs: update runbook and mutagen setup"` → `git push`.

## Полезные ссылки
- Mutagen: https://mutagen.io/documentation/sync
- Docker Compose healthcheck best practices: https://docs.docker.com/compose/compose-file/05-services/#healthcheck
- Grafana provisioning: `infra/grafana/`
- Observability configs: `infra/prometheus/`, `infra/otel/`, `infra/loki/`, `infra/blackbox/`, `infra/tempo/`


