Dent‑AI — Project Context (for future sessions)
Current state (as of today)
New production server: 155.212.135.25 (root), Ubuntu 24.04.
Old server 158.160.203.51 fully cleaned: all containers/volumes removed, server shut down.
All core services on new server are UP & healthy:
app (FastAPI, port 8000) — /docs returns 200
bot (aiogram) — polling, updates processed
postgres, redis, qdrant, minio, vault — healthy
Vault auto‑unseal restored and working (Sealed: false).
Infrastructure / Stack
Services in docker-compose.yml:
app, bot
postgres (pgvector), qdrant
redis
minio
vault
postgres-backup
autoheal
Optional observability profile (Grafana/Prometheus/Loki/Tempo/OTEL/Promtail/Blackbox) was running on old server; on new server only core stack.
Whisper uses CPU by default.
Migration notes
Data migrated: Postgres, Qdrant, MinIO, Redis (appendonlydir + dump.rdb), Vault (volume).
Redis AOF located at /data/appendonlydir/.
.env had CRLF issues; use sanitized .env when sourcing.
Vault storage was restored from old volume; required chown and auto‑unseal script.
Vault auto‑unseal
Env in .env:
VAULT_UNSEAL_STRATEGY=lockbox
VAULT_UNSEAL_LOCKBOX_SECRET_ID=...
VAULT_UNSEAL_LOCKBOX_ENTRY_PREFIX=vault-unseal-key-
VAULT_UNSEAL_SA_KEY_FILE=/etc/dent-ai/lockbox-sa-key.json
Script: infra/scripts/vault-auto-unseal.sh
If sealed: run bash infra/scripts/vault-auto-unseal.sh.
Security note: unseal keys and IAM token were accidentally exposed in chat; must rotate (rekey) and update Lockbox.
Deployment
GitHub Actions used previously to build/push to Yandex Container Registry.
Yandex Container Registry deleted.
For new deploy: GitHub Actions should deploy via SSH to new server and build images on the server (docker compose build && docker compose up -d) or do git pull then build.
Secrets updated in GitHub:
DEPLOY_HOST=155.212.135.25
DEPLOY_USER=root
DEPLOY_PATH=/srv/dent_ai
DEPLOY_SSH_KEY = private key for dent-ai-deploy
SSH key for deploy
Public key added on new server:
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF0nbBh3G2MP3o2n/fT0wlQZRNgoekCoUYf1smGmejWz plove@DESKTOP-917G9L6
Private key stored locally:
C:\Users\plove\.ssh\dent-ai-deploy
Checks (all green)
Run on new server:
```bash
docker compose ps
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs   # 200
docker exec dent_ai_vault vault status  # Sealed: false
docker exec dent_ai_postgres pg_isready -U dent_ai
docker exec dent_ai_redis redis-cli -a "<REDIS_PASSWORD>" ping  # PONG
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6333/healthz # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live # 200
```
Data / DB
Postgres DB name: dent_ai
Tables present: doctor_profiles, doctors, patients, plan_feedback, plan_templates, sessions, treatment_plans
Qdrant collection: plan_templates_v1
Bot behavior note
On new server bot asked for profile again. Cause: FSM state reset. Profiles exist in DB; it’s one‑time and ok.
Local workspace
New local working directory created from server archive:
C:\dent_ai_work\dent_ai
Archive created on server:
/root/dent_ai_code.tar.gz
.env copied separately (contains secrets).
Next recommended actions
1) Rotate Vault unseal keys (vault operator rekey) and update Lockbox secret.
2) Update GitHub Actions workflow to deploy without Yandex registry (build on server).
3) Confirm CI/CD works with new server.
