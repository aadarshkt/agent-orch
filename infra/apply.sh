#!/usr/bin/env bash
# Wrapper around OpenTofu that injects secrets from local files, so they never
# end up in the shell history, a tfvars file, or Terraform state you share.
#
#   ./apply.sh init
#   ./apply.sh plan
#   ./apply.sh apply
#
# Secret sources (first match wins):
#   backend/keys/openrouter-key.txt   or  OPENROUTER_API_KEY= in backend/.env
#   backend/keys/github-token.txt     or  GITHUB_TOKEN= in backend/.env
#   backend/keys/ghcr-token.txt       or  $GHCR_TOKEN
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

REPO_ROOT="$(cd .. && pwd)"
KEYS="$REPO_ROOT/backend/keys"
ENV_FILE="$REPO_ROOT/backend/.env"

if ! command -v tofu >/dev/null 2>&1; then
  echo "tofu not found on PATH. Install OpenTofu: https://opentofu.org/docs/intro/install/" >&2
  exit 1
fi

read_secret() {
  local file="$1" env_key="$2"
  if [[ -f "$file" ]]; then
    tr -d '\r\n' < "$file"
  elif [[ -f "$ENV_FILE" && -n "$env_key" ]]; then
    grep -m1 "^${env_key}=" "$ENV_FILE" | cut -d= -f2- || true
  fi
  return 0
}

OPENROUTER_API_KEY="$(read_secret "$KEYS/openrouter-key.txt" OPENROUTER_API_KEY)"
GITHUB_TOKEN="$(read_secret "$KEYS/github-token.txt" GITHUB_TOKEN)"
GHCR_TOKEN_FILE="$(read_secret "$KEYS/ghcr-token.txt" GHCR_TOKEN)"

# init / fmt / validate don't need variable values; plan and apply do.
if [[ -z "$OPENROUTER_API_KEY" ]]; then
  case "${1:-}" in
    plan|apply|destroy|console)
      echo "Missing OpenRouter key. Put it in backend/keys/openrouter-key.txt" >&2
      exit 1
      ;;
    *)
      echo "note: no OpenRouter key found (needed for plan/apply)" >&2
      ;;
  esac
fi

if [[ -n "$OPENROUTER_API_KEY" ]]; then
  export TF_VAR_openrouter_api_key="$OPENROUTER_API_KEY"
fi
[[ -n "$GITHUB_TOKEN" ]] && export TF_VAR_github_token="$GITHUB_TOKEN"
[[ -n "${GHCR_TOKEN:-}" ]] && export TF_VAR_ghcr_token="$GHCR_TOKEN"
[[ -z "${GHCR_TOKEN:-}" && -n "$GHCR_TOKEN_FILE" ]] && export TF_VAR_ghcr_token="$GHCR_TOKEN_FILE"

exec tofu "$@"
