Deploy Runbook — Dent‑AI (New Server)
Note: актуальные данные по хосту/SSH/секретам в `docs/production.md`.
Goal
Deploy to 155.212.135.25 via GitHub Actions using SSH and server‑side build (no Yandex Registry).
Prerequisites
New server running: 155.212.135.25
SSH key added to ~/.ssh/authorized_keys on server
GitHub Secrets configured:
DEPLOY_HOST = 155.212.135.25
DEPLOY_USER = root
DEPLOY_PATH = /srv/dent_ai
DEPLOY_SSH_KEY = private key (OpenSSH format)
GitHub Actions workflow (concept)
Deploy step should do:
1) cd /srv/dent_ai
2) git fetch --all
3) git reset --hard origin/main
4) docker compose build app bot postgres-backup
5) docker compose up -d
6) (Optional) docker compose ps
Example deploy script (SSH)
```bash
cd /srv/dent_ai
git fetch --all
git reset --hard origin/main
docker compose build app bot postgres-backup
docker compose up -d
docker compose ps
```
How to update workflow (if needed)
Edit .github/workflows/ci.yml:
Remove steps that push to registry (Yandex)
In deploy job, use SSH action to run the script above
Ensure workflow uses DEPLOY_* secrets
Manual deploy (server)
If CI is down:
```bash
cd /srv/dent_ai
git pull
docker compose build app bot postgres-backup
docker compose up -d
```
Post‑deploy checks
```bash
docker compose ps
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs   # expect 200
docker exec dent_ai_vault vault status                               # Sealed: false
docker exec dent_ai_postgres pg_isready -U dent_ai
docker exec dent_ai_redis redis-cli -a "<REDIS_PASSWORD>" ping        # PONG
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6333/healthz # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live # 200
```
Troubleshooting
If build fails on server: check docker logs for app/bot.
If vault sealed: run bash infra/scripts/vault-auto-unseal.sh.
If Redis auth fails: verify .env has correct REDIS_PASSWORD.
If /docs returns 404: service running but route missing (normal).
If bot re‑asks profile: FSM reset, one‑time.
Security notes
Rotate Vault unseal shares (rekey) after any exposure.
Rotate IAM tokens used for registry if still active.