## Dent AI Автопилот — единый бэклог

### Контекст
- Цель: полностью безручковый деплой/эксплуатация Dent AI в Yandex Cloud (образа → регистр → прод), zero-touch релизы, автоподнятие после падений, предсказуемые бэкапы, непрерывный мониторинг.
- Текущая база: монолитный docker-compose стек на YC VM, GitHub Actions черновик, ручные `.env`, Vault/MinIO подняты, ручное управление SG/VM.

### Стрим 1 — Управление инфраструктурой
- 🆕 `iac:pulumi-yc-core` — описать VPC, subnet, NAT, SG, диски, сервисные аккаунты в Pulumi (Python) + стейт в YC Object Storage, автоматическая выдача IAM ролей.
- 🆕 `iac:pulumi-compute` — зафиксировать VM спецификацию (ядра, RAM, диск), cloud-init (docker, compose plugin, otel-collector agent). Добавить managed instance group для автозамены и health-check.
- 🆕 `iac:pulumi-secrets` — описать YC Lockbox/KeyVault, MinIO bucket, версионирование бэкапов, политики жизненного цикла.
- 🆕 `iac:pulumi-network-egress` — настроить Private DNS, статический внешний IP, HTTPS termination (YC Application Load Balancer) с автоматическим Let's Encrypt.
- 🔭 `research:managed-postgres` — оценить миграцию на Yandex Managed PostgreSQL с автоматическими обновлениями/бэкапами.

### Стрим 2 — CI/CD 100% автоматизация
- 🚧 `ci:gha-pipeline` — матрица тестов (unit, интеграционные, contracts) + запуск миграций в ephemeral postgres (Testcontainers).
- 🆕 `ci:build-cache` — buildx + кэш через `actions/cache`/YCR, сборка multi-arch (linux/amd64, linux/arm64) с SBOM (Syft) и подписью (cosign) через OIDC.
- 🆕 `ci:scan` — SAST (Semgrep), dependency audit (pip-audit), контейнерный скан (grype) с fail-on-high.
- 🚧 `cd:ssh-zero-touch` — GitHub Actions deploy job: пуш в YCR, скачивание compose bundle через `docker compose --profile prod pull`, health probe `/ping`, auto-rollback при 5xx/timeout.
- 🆕 `cd:canary` — развертывание в два compose-профиля (`prod-a`/`prod-b`) + трафик через ALB, автоматический cutover после success window.
- 🆕 `cd:chatops` — slash-команды в Telegram (через бота) для `deploy`, `roll back`, `status` с аутентификацией по Vault JWT.

### Стрим 3 — Управление конфигурацией и секретами
- 🆕 `cfg:auto-env` — генератор `.env` (PowerShell + Bash) из шаблона, подтягивает секреты из Vault/Lockbox, проверяет расхождения с `.github/workflows` и compose.
- 🆕 `cfg:secret-lint` — pre-commit hook + CI job, контролирует дубли/утечки (`detect-secrets`, `trufflehog`), сверяет срок ротации токенов.
- 🆕 `cfg:vault-bootstrap` — автоматика инициализации/разблокировки Vault: systemd service для авто-unseal через shamir keys в Lockbox, sync policy + динамические креды Postgres/MinIO.
- 🆕 `cfg:rbac` — RBAC матрица (GitHub, YC, Vault, MinIO, Grafana) с описанием ролей и Terraform-политиками.

### Стрим 4 — Данными и миграции
- ✅ `db:auto-migrate` — стартовые миграции через `infra/docker/app-entrypoint.sh` (уже внедрено).
- 🆕 `db:alembic` — переход на Alembic + автогенерация ревизий, хранение в репо, миграции прогоняются в CI (dry-run) и на прод через `alembic upgrade head`.
- 🆕 `db:backup-ops` — supercronic → уведомления в Telegram (успех/ошибка), проверка восстановления (`pg_restore --list`), weekly disaster recovery drill.
- 🆕 `db:pit` — Point-in-Time Recovery с помощью WAL-G (MinIO backend), тестовый recovery pipeline в отдельном контейнере.

### Стрим 5 — Наблюдаемость и самовосстановление
- 🆕 `obs:loki-grafana-provision` — автопровижен dashboards/alerts (JSON in repo), alerter → Telegram/Email.
- 🆕 `obs:tempo` — добавить трассировку (Tempo) + автоматический экспорт из FastAPI/бота через OTLP.
- 🆕 `obs:slo` — определить SLI/SLO (доступность API, латентность, задержка очередей), настроить error budget burn alerts.
- 🆕 `obs:autoheal` — systemd watchdog + `docker events` контроллер, рестарт/перечек тасков, интеграция с YC Monitoring auto-restart.
- 🆕 `obs:synthetics` — k6/cloud сценарии с cron, результаты в Grafana Cloud или YC Monitoring.

### Стрим 6 — QA и эксплуатация
- 🆕 `qa:contract-tests` — OpenAPI snapshot-тесты, Postman/newman regression при каждом релизе.
- 🆕 `qa:bot-sim` — headless Telegram simulator (grizzly / pyrogram) гоняет сценарии с mock backend.
- 🆕 `ops:runbook` — единый Runbook: bootstrap, disaster recovery, incident-response, SLA.
- 🆕 `ops:training` — записать скринкасты для on-call, упаковать в internal portal.

### Done / В работе (статус синхронизирован)
- ✅ `.env` заполнен, сервисы healthy на prod VM.
- ✅ Vault/MinIO базовая инициализация + ручные ключи.
- ✅ `docker compose` профили `observability` подключены при старте.
- 🚧 CI/CD pipeline в GitHub Actions — черновик создан, требует доработки.

### Следующие шаги (приоритет блоков)
1. Закрыть `cfg:auto-env` + `cfg:secret-lint` для устранения ручных действий с конфигами.
2. Довести `ci:gha-pipeline` и `cd:ssh-zero-touch` до production-grade + покрыть `ci:scan`.
3. Обвязать инфраструктуру Pulumi (`iac:*`), чтобы VM/SG не правились руками.
4. Расширить наблюдаемость: `obs:loki-grafana-provision`, `obs:tempo`, `obs:autoheal`.
5. Финализировать `db:alembic` и `db:backup-ops` перед включением canary-релизов.

Статус документа обновляется при каждом мёрдже соответствующих задач (PR label = `roadmap`).

