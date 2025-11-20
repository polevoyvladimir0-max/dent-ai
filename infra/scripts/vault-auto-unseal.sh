#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[vault-auto-unseal] %s\n' "$*"
}

abort() {
  printf '[vault-auto-unseal][error] %s\n' "$*" >&2
  exit 1
}

PROJECT_DIR="${PROJECT_DIR:-/srv/dent_ai}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

if [ -f "$ENV_FILE" ]; then
  log "Loading environment from $ENV_FILE"
  set -a
  # Безопасная загрузка .env: парсим только KEY=VALUE строки, игнорируя комментарии и пустые строки
  while IFS= read -r line || [ -n "$line" ]; do
    # Убираем \r для Windows line endings
    line=$(printf '%s' "$line" | tr -d '\r')
    # Пропускаем комментарии и пустые строки
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    # Пропускаем строки без =
    [[ ! "$line" =~ = ]] && continue
    # Экспортируем переменную безопасно
    # Извлекаем ключ и значение
    key="${line%%=*}"
    value="${line#*=}"
    # Убираем пробелы вокруг ключа
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    # Убираем кавычки вокруг значения, если они есть
    if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:-1}"
    fi
    # Убираем пробелы вокруг значения
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    # Экспортируем
    export "$key=$value" 2>/dev/null || true
  done < "$ENV_FILE"
  set +a
fi

COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"
VAULT_SERVICE_NAME="${VAULT_SERVICE_NAME:-vault}"
VAULT_CONTAINER_NAME="${VAULT_CONTAINER_NAME:-dent_ai_vault}"
STRATEGY="${VAULT_UNSEAL_STRATEGY:-lockbox}"
LOCKBOX_SECRET_ID="${VAULT_UNSEAL_LOCKBOX_SECRET_ID:-}"
LOCKBOX_ENTRY_PREFIX="${VAULT_UNSEAL_LOCKBOX_ENTRY_PREFIX:-vault-unseal-key-}"
SA_KEY_FILE="${VAULT_UNSEAL_SA_KEY_FILE:-/etc/dent-ai/lockbox-sa-key.json}"
UNSEAL_FILE="${VAULT_UNSEAL_FILE:-}"
YC_BIN="${YC_BIN:-yc}"
JQ_BIN="${JQ_BIN:-jq}"
HEALTH_WAIT_SECONDS="${VAULT_UNSEAL_WAIT:-300}"

cd "$PROJECT_DIR"

if ! command -v $JQ_BIN >/dev/null 2>&1; then
  abort "jq is required for vault-auto-unseal"
fi

if ! $COMPOSE_CMD ps --services | grep -qx "$VAULT_SERVICE_NAME"; then
  abort "Vault service '$VAULT_SERVICE_NAME' is not defined in docker compose"
fi

is_unsealed() {
  local sealed
  sealed=$($COMPOSE_CMD exec -T "$VAULT_SERVICE_NAME" vault status -format=json 2>/dev/null | $JQ_BIN -r '.sealed' || echo "true")
  [[ "$sealed" == "false" ]]
}

wait_for_vault() {
  local deadline code
  deadline=$(( $(date +%s) + HEALTH_WAIT_SECONDS ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if $COMPOSE_CMD exec -T "$VAULT_SERVICE_NAME" vault status >/dev/null 2>&1; then
      return 0
    else
      code=$?
      if [ "$code" -eq 2 ]; then
        return 0
      fi
    fi
    sleep 5
  done
  return 1
}

if is_unsealed; then
  log "Vault is already unsealed"
  exit 0
fi

if ! wait_for_vault; then
  abort "Vault API did not become reachable"
fi

collect_shares() {
  case "$STRATEGY" in
    lockbox)
      [ -n "$LOCKBOX_SECRET_ID" ] || abort "VAULT_UNSEAL_LOCKBOX_SECRET_ID is not set"
      [ -f "$SA_KEY_FILE" ] || abort "Service account key '$SA_KEY_FILE' not found"
      command -v $YC_BIN >/dev/null 2>&1 || abort "yc CLI is required for lockbox strategy"
      local token payload
      token=$(YC_SERVICE_ACCOUNT_KEY_FILE="$SA_KEY_FILE" $YC_BIN iam create-token)
      payload=$(YC_TOKEN="$token" $YC_BIN lockbox payload get --id "$LOCKBOX_SECRET_ID" --format json)
      echo "$payload" | $JQ_BIN -r --arg prefix "$LOCKBOX_ENTRY_PREFIX" '.entries[] | select(.key | startswith($prefix)) | .text_value'
      ;;
    file)
      [ -n "$UNSEAL_FILE" ] || abort "VAULT_UNSEAL_FILE is not set"
      [ -f "$UNSEAL_FILE" ] || abort "Unseal file '$UNSEAL_FILE' not found"
      grep -vE '^(#|\s*$)' "$UNSEAL_FILE"
      ;;
    disabled)
      log "Auto-unseal strategy is disabled"
      exit 0
      ;;
    *)
      abort "Unknown VAULT_UNSEAL_STRATEGY '$STRATEGY'"
      ;;
  esac
}

mapfile -t SHARES < <(collect_shares)

if [ "${#SHARES[@]}" -lt 3 ]; then
  abort "Need at least 3 unseal shares, got ${#SHARES[@]}"
fi

for share in "${SHARES[@]}"; do
  if [ -z "$share" ]; then
    continue
  fi
  log "Applying unseal share"
  $COMPOSE_CMD exec -T "$VAULT_SERVICE_NAME" vault operator unseal "$share" >/dev/null
  if is_unsealed; then
    log "Vault successfully unsealed"
    exit 0
  fi
  sleep 1
done

if is_unsealed; then
  log "Vault successfully unsealed"
  exit 0
fi

abort "Failed to unseal Vault with provided shares"
