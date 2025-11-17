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

## Авто-unseal Vault
- `VAULT_UNSEAL_STRATEGY` — режим (`lockbox`, `file`, `disabled`). По умолчанию `lockbox`.
- `VAULT_UNSEAL_LOCKBOX_SECRET_ID` — ID секрета в Yandex Lockbox, в котором лежат шарды `vault operator unseal` (`vault-unseal-key-1`, `vault-unseal-key-2`, ...).
- `VAULT_UNSEAL_LOCKBOX_ENTRY_PREFIX` — префикс ключей в секрете (можно оставить `vault-unseal-key-`).
- `VAULT_UNSEAL_SA_KEY_FILE` — путь к JSON ключу сервисного аккаунта с доступом к Lockbox (на сервере, не коммитим).
- `VAULT_UNSEAL_FILE` — fallback на локальный файл (если стратегия `file`).
- `YC_BIN` — полный путь до `yc` (если CLI стоит не в системном PATH).

## YandexGPT
- `YANDEX_GPT_API_KEY` — API-ключ YandexGPT (выдаём сервисному аккаунту роль `ai.languageModels.user`).
- `YANDEX_GPT_FOLDER_ID` — ID каталога, где доступна модель.
- `YANDEX_GPT_MODEL` — имя модели (`yandexgpt-lite`, `yandexgpt/latest` и т.п.).
- `YANDEX_GPT_TIMEOUT` — таймаут ожидания ответа (сек).

Скрипт `infra/scripts/vault-auto-unseal.sh` вызывается деплой-джобой после `docker compose up`, поэтому после рестарта/деплоя Vault автоматически разблокируется, если:
1. На сервере установлен `yc` CLI (`curl https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash`).
2. Есть `jq` (`sudo apt-get install jq`).
3. Сервисному аккаунту выдан доступ `lockbox.payloadViewer` к нужному секрету.
4. JSON ключ SA сохранён, например, в `/etc/dent-ai/lockbox-sa-key.json` (600/root:root) и root однажды выполнил `sudo /home/ubuntu/yandex-cloud/bin/yc config set service-account-key /etc/dent-ai/lockbox-sa-key.json`.
5. В `.env` заданы переменные выше и пересобран через `generate-env.ps1`.

В секрете Lockbox создаём, например, три записи `vault-unseal-key-1`…`-3` и кладём туда исходные Shamir-ключи из инициализации Vault. Скрипт подхватывает значения автоматически.

