# The validation sandbox (ADR 009, 019): Python, uv, and the checks' tools in their own
# venv. Generated code never runs while the image builds. Each validation brings it in
# a fresh volume, and the code's dependencies in a second one.
#
# Build: uv run python -m aidev.validation build-image   (from backend/)

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

# The tools live apart from the code's dependencies (/deps), so a generated
# requirements.txt can't replace them. pyright[nodejs] brings its own Node.js, so it
# never downloads anything at run time.
COPY tools.txt /opt/tools.txt
RUN uv venv /opt/tools \
    && VIRTUAL_ENV=/opt/tools uv pip install --no-cache -r /opt/tools.txt \
    && /opt/tools/bin/pyright --version

# The checks' configuration, outside the workspace: code under validation can't
# loosen its own rules.
COPY ruff.toml pyrightconfig.json /etc/aidev/

RUN useradd --uid 10001 --create-home sandbox \
    && mkdir /deps /work /uv-cache \
    && chown sandbox:sandbox /deps /work /uv-cache
USER sandbox

# /uv-cache is a volume shared by install steps (sandbox.py); copies, not hardlinks,
# because it and /deps are different volumes.
ENV PATH=/opt/tools/bin:$PATH \
    PYTHONPATH=/deps:/work \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/uv-cache \
    UV_LINK_MODE=copy \
    HOME=/tmp
WORKDIR /work
