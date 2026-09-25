#!/usr/bin/env bash
# Build and push the backend image and the CLI-agent images to GHCR.
#
# Usage:
#   deploy/push-images.sh [target ...]     # no args = all targets
# Targets: backend test-cli openrouter-agent aider-agent
#
# Auth: run `docker login ghcr.io` first, or set GHCR_USERNAME + GHCR_TOKEN.
# Overridable: REGISTRY (ghcr.io), GHCR_OWNER, TAG (latest), PLATFORM.
#
# PLATFORM defaults to linux/amd64 because the EC2 host is x86_64 — building on
# an arm64 Mac without this produces images the host cannot run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REGISTRY="${REGISTRY:-ghcr.io}"
OWNER="${GHCR_OWNER:-aadarshkt}"
TAG="${TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

if [[ -n "${GHCR_USERNAME:-}" && -n "${GHCR_TOKEN:-}" ]]; then
  echo "Logging in to ${REGISTRY} as ${GHCR_USERNAME}"
  printf '%s' "$GHCR_TOKEN" | docker login "$REGISTRY" -u "$GHCR_USERNAME" --password-stdin
fi

build_and_push() {
  local image="$1" context="$2"
  shift 2
  echo "Building ${image} (${PLATFORM})"
  docker buildx build --platform "$PLATFORM" -t "$image" --push "$@" "$context"
}

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=(backend test-cli openrouter-agent aider-agent)
fi

for target in "${targets[@]}"; do
  # The GHCR repository names must match the `image:` values in
  # backend/config/runtimes.yaml, hence the explicit mapping.
  case "$target" in
    backend)
      build_and_push "${REGISTRY}/${OWNER}/agent-orch-backend:${TAG}" \
        "$REPO_ROOT" -f "$SCRIPT_DIR/backend.Dockerfile"
      ;;
    test-cli)
      build_and_push "${REGISTRY}/${OWNER}/agent-orch-test-cli:${TAG}" \
        "$REPO_ROOT/backend/docker/test-agent"
      ;;
    openrouter-agent)
      build_and_push "${REGISTRY}/${OWNER}/agent-orch-openrouter:${TAG}" \
        "$REPO_ROOT/backend/docker/openrouter-agent"
      ;;
    aider-agent)
      build_and_push "${REGISTRY}/${OWNER}/agent-orch-aider:${TAG}" \
        "$REPO_ROOT/backend/docker/aider-agent"
      ;;
    *)
      echo "Unknown target: ${target}" >&2
      exit 1
      ;;
  esac
done

echo "Pushed ${#targets[@]} image(s) to ${REGISTRY}/${OWNER}."
