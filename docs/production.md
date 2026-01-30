# Production Environment (Dent AI)

Актуальные данные по прод‑окружению, чтобы не разъезжалось между файлами.

## Сервер и доступ
- Хост: `155.212.135.25`
- Пользователь: `root`
- Путь проекта: `/srv/dent_ai`
- SSH‑ключ: `C:/Users/plove/.ssh/dent-ai-deploy`

Пример записи в `~/.ssh/config`:
```
Host dent-ai-vm
    HostName 155.212.135.25
    User root
    IdentityFile C:/Users/plove/.ssh/dent-ai-deploy
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
```

## CI/CD secrets (GitHub Actions)
- `DEPLOY_HOST=155.212.135.25`
- `DEPLOY_USER=root`
- `DEPLOY_PATH=/srv/dent_ai`
- `DEPLOY_SSH_KEY` = private key (OpenSSH)

## Проверки после деплоя
```bash
docker compose ps
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs   # 200
docker exec dent_ai_vault vault status  # Sealed: false
docker exec dent_ai_postgres pg_isready -U dent_ai
docker exec dent_ai_redis redis-cli -a "<REDIS_PASSWORD>" ping  # PONG
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6333/healthz # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live # 200
```
