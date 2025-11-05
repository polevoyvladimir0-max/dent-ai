# Генератор окружения

`schema.json` описывает все переменные для двух таргетов:
- `compose` — локальный docker-compose/продовый сервер (порты, креды сервисов);
- `ci` — секреты и хосты, которые нужны GitHub Actions при деплое.

`secrets.sample.json` — образец JSON-файла с чувствительными значениями. Подставь реальные значения и сохрани файл рядом (например, `secrets.local.json`).

## Быстрый старт (Windows)

```powershell
pwsh ./infra/scripts/generate-env.ps1 -Target compose -SecretsPath ./infra/env/secrets.local.json -OutputPath ./.env -Force
```

- `-Profile prod` — переключает профиль (значения из `schema.json.profiles.prod`).
- `-DryRun` — только валидация, без записи файла.
- `-Target ci` — собрать `.env.ci` с секретами для GitHub Actions (`REGISTRY_*`, `DEPLOY_*`).

## GitHub Actions / Linux

На раннерах можно вызвать тот же скрипт через `pwsh`:

```yaml
- name: Render .env for compose
  shell: pwsh
  run: ./infra/scripts/generate-env.ps1 -Target compose -SecretsPath ./infra/env/secrets.ci.json -OutputPath .env.compose
```

Файл `secrets.ci.json` лучше собирать в job из GitHub Secrets (см. пример в Roadmap).

## Особенности валидации
- Все обязательные переменные проверяются на `minLength` и регулярки.
- Если секрет выглядит как шаблон (`changeme`, `example`, `replace_me`…), генератор падает.
- При смешанном JSON (в одном файле CI и compose-секреты) появится предупреждение о незнакомых ключах — это норма.

Фактический `.env` по-прежнему остаётся вне репозитория — так риск утечек минимальный.

## Наблюдаемость
- `GRAFANA_ALERT_TELEGRAM_BOT_TOKEN` / `GRAFANA_ALERT_TELEGRAM_CHAT_ID` — опциональные переменные для мгновенных алертов в Telegram.
- `PROMETHEUS_PORT`, `PROMETHEUS_RETENTION` — управление Prometheus без редактирования compose.
- `AUTOHEAL_INTERVAL`, `AUTOHEAL_START_PERIOD` — тюнинг автоматического перезапуска контейнеров.

