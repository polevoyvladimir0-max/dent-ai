# GitOps Playbooks

## Бэкапы
- `invoke-backup.ps1` — создаёт zip архив с ключевыми артефактами.
  - Параметры: `-SourceRoot`, `-BackupDir`, флаг `-IncludeStorage`.
  - По умолчанию складывает архивы в `C:\dent_ai\backups`.

## Деплой
- `deploy.ps1`
  - Обновляет прайсы (`scripts/extract_pricing.py`), прогоняет sanity-check.
  - Загружает эмбеддинги в Qdrant (если не указан `-SkipIngest`).
  - Поднимает стэк через `docker compose up -d --build`.

## Ротация
- Рекомендуется хранить >=5 последних бэкапов.
- Автоматизировать удаление старых архивов можно через планировщик задач Windows.
