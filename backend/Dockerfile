# API image. Build from the repo root so the curated corpus/ can be copied in:
#   docker build -f backend/Dockerfile .
# The layout mirrors the repo (/app/backend, /app/corpus) because Settings resolves
# corpus_dir relative to the source tree.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend

# Dependencies first, so source edits don't reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY backend/ ./
RUN uv sync --locked --no-dev


FROM python:3.12-slim
RUN useradd --create-home --uid 1000 aidev
WORKDIR /app/backend
COPY --from=build /app/backend /app/backend
COPY corpus/ /app/corpus/
RUN mkdir /data && chown aidev /data

ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
USER aidev
EXPOSE 8000

# Proxy headers on: behind Caddy, the per-IP rate limit must see the visitor's address,
# not Caddy's. Only Caddy can reach this port, so trusting its headers is safe.
CMD ["uvicorn", "aidev.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
