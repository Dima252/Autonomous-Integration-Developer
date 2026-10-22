#!/usr/bin/env sh
# Rolls the stack out to the image tag given (default: latest). Runs on the VM from
# /opt/aidev; CD calls it after copying the new compose file and Caddyfile.
set -eu
cd "$(dirname "$0")"
export IMAGE_TAG="${1:-latest}"

docker compose pull --quiet api web
# `run` starts postgres and redis first and waits until they're healthy.
docker compose run --rm api alembic upgrade head
docker compose up -d --remove-orphans --wait
docker image prune -f
echo "deployed ${IMAGE_TAG}"
