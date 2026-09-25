#!/usr/bin/env bash
# First-boot provisioning for the agent-orch backend host.
#
# Run once by cloud-init (via /opt/agent-orch/deploy.conf for its inputs).
# Idempotent — safe to re-run by hand after editing deploy.conf.
#
#   /opt/agent-orch/deploy.conf            inputs (domain, image tag, ssm prefix)
#   /opt/agent-orch/docker-compose.yml     host stack definition
#   /opt/agent-orch/Caddyfile              TLS reverse proxy config
#   /mnt/data/agent-orch/backend.env       generated secrets/config (root, 0600)
set -euo pipefail

STACK=/opt/agent-orch
CONF="$STACK/deploy.conf"
DATA=/mnt/data
ROOT="$DATA/agent-orch"

# shellcheck source=/dev/null
source "$CONF"

log() { echo "[bootstrap] $*"; }

# ── 0. AWS CLI ────────────────────────────────────────────────────────────────
# Ubuntu 24.04 dropped the `awscli` apt package (it was Python 2 based) and a
# missing one makes the whole cloud-init package step fail, so install the
# official v2 build here instead of relying on `packages:`.
ensure_awscli() {
  if command -v aws >/dev/null 2>&1; then
    return 0
  fi

  log "Installing AWS CLI v2"
  local arch tmp
  arch="$(uname -m)"
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip" -o "$tmp/awscliv2.zip"

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  else
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' \
      "$tmp/awscliv2.zip" "$tmp"
  fi

  chmod +x "$tmp/aws/install"
  "$tmp/aws/install" --update
  rm -rf "$tmp"

  if ! command -v aws >/dev/null 2>&1; then
    log "ERROR: AWS CLI install failed"
    exit 1
  fi
}

ensure_awscli

# ── 1. Attach and mount the data volume ───────────────────────────────────────
DEV=""
for _ in $(seq 1 30); do
  for candidate in /dev/nvme1n1 /dev/xvdf /dev/sdf; do
    if [ -b "$candidate" ]; then
      DEV="$candidate"
      break 2
    fi
  done
  sleep 2
done
if [ -z "$DEV" ]; then
  log "ERROR: no data volume found"
  exit 1
fi

install -d "$DATA"
if ! blkid "$DEV" >/dev/null 2>&1; then
  log "Formatting $DEV"
  mkfs.ext4 -q "$DEV"
fi
if ! mountpoint -q "$DATA"; then
  log "Mounting $DEV at $DATA"
  mount "$DEV" "$DATA"
fi

VOL_UUID="$(blkid -s UUID -o value "$DEV")"
if ! grep -q "$VOL_UUID" /etc/fstab; then
  echo "UUID=$VOL_UUID $DATA ext4 defaults,nofail 0 2" >> /etc/fstab
fi

install -d "$ROOT/workspaces" "$ROOT/.docker" "$ROOT/pgdata" "$ROOT/caddy"

# ── 2. Runtime secrets from SSM Parameter Store ───────────────────────────────
get_param() {
  aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "${SSM_PREFIX}/$1" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text
}

# Optional secrets are only created in SSM when a value was supplied.
get_param_optional() {
  get_param "$1" 2>/dev/null || echo ""
}

POSTGRES_PASSWORD="$(get_param postgres_password)"
OPENROUTER_API_KEY="$(get_param openrouter_api_key)"
GITHUB_TOKEN="$(get_param_optional github_token)"
GHCR_TOKEN="$(get_param_optional ghcr_token)"

ENV_FILE="$ROOT/backend.env"
umask 077
{
  echo "DOMAIN=$DOMAIN"
  echo "BACKEND_IMAGE=$BACKEND_IMAGE"
  echo "POSTGRES_USER=$POSTGRES_USER"
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
  echo "POSTGRES_DB=$POSTGRES_DB"
  echo "DATABASE_URL=postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB?sslmode=disable"
  echo "CORS_ORIGINS=$CORS_ORIGINS"
  echo "WORKSPACE_ROOT=$ROOT/workspaces"
  echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY"
  echo "GITHUB_TOKEN=$GITHUB_TOKEN"
  if [ -n "$GHCR_TOKEN" ]; then
    echo "REGISTRY_URL=ghcr.io"
    echo "REGISTRY_USERNAME=$GHCR_USERNAME"
    echo "REGISTRY_PASSWORD=$GHCR_TOKEN"
  fi
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
log "Wrote $ENV_FILE"

# ── 3. Docker + stack ─────────────────────────────────────────────────────────
systemctl enable --now docker
if [ -n "$GHCR_TOKEN" ]; then
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
else
  log "No GHCR token supplied — assuming the images are public"
fi

docker compose --env-file "$ENV_FILE" -f "$STACK/docker-compose.yml" pull
docker compose --env-file "$ENV_FILE" -f "$STACK/docker-compose.yml" up -d --remove-orphans

log "Stack is up:"
docker compose --env-file "$ENV_FILE" -f "$STACK/docker-compose.yml" ps
