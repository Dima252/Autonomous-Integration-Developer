#!/usr/bin/env sh
# Fills an empty database: API catalog, docs index, embeddings. Run once on the VM after
# the first deploy. Every step is idempotent, and `embed` resumes where it stopped, so
# it's safe to re-run. At Voyage's free-account pacing, embedding takes ~2.5 h; run this
# inside tmux or with nohup.
set -eu
cd "$(dirname "$0")"

docker compose run --rm api python -m aidev.catalog ingest
docker compose run --rm api python -m aidev.rag index
docker compose run --rm api python -m aidev.rag embed
docker compose run --rm api python -m aidev.rag status
