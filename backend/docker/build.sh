#!/usr/bin/env bash
# Build and push a CLI agent image to the registry.
# Usage: build.sh <image> [context_dir]
set -euo pipefail

IMAGE="${1:?usage: build.sh <image> [context_dir]}"
CONTEXT="${2:-.}"

docker build -t "${IMAGE}" -f "${CONTEXT}/agent.Dockerfile" "${CONTEXT}"
docker push "${IMAGE}"
echo "Pushed ${IMAGE}"
