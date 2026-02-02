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

## Vault auto‑unseal без Яндекса (Transit)
Ключи unseal хранятся в отдельном Vault (seal‑vault). Основной Vault разлочивается через transit.

1) На seal‑vault (отдельная VM/контейнер) включи transit и создай ключ:
```bash
vault secrets enable transit
vault write -f transit/keys/dent-ai-unseal
```

2) Создай policy и токен для auto‑unseal:
```hcl
# policy: dent-ai-unseal.hcl
path "transit/encrypt/dent-ai-unseal" { capabilities = ["update"] }
path "transit/decrypt/dent-ai-unseal" { capabilities = ["update"] }
```
```bash
vault policy write dent-ai-unseal dent-ai-unseal.hcl
vault token create -policy=dent-ai-unseal
```

3) В проде (основной Vault) включи transit‑конфиг:
```bash
VAULT_CONFIG_FILE=config.transit.hcl
VAULT_SEAL_ADDR=https://<seal-vault-host>:8200
VAULT_SEAL_TOKEN=<token>
VAULT_SEAL_KEY_NAME=dent-ai-unseal
VAULT_SEAL_MOUNT_PATH=transit/
VAULT_SEAL_TLS_SKIP_VERIFY=true  # только если самоподписанный TLS
```

4) Удали/отключи старые `VAULT_UNSEAL_*` секреты в GitHub, чтобы deploy не пытался использовать Yandex.

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
