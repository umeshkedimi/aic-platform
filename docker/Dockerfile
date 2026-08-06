# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder — resolves and installs dependencies into a venv.
#
# Split from the runtime stage so build tooling (uv itself, compilers pulled
# in by any dependency with a C extension) never ships in the image that
# actually takes traffic.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# Dependencies are installed from the lockfile alone, before any source is
# copied in. Application code changes on nearly every commit; dependencies
# change rarely — this ordering means the expensive layer (dependency
# resolution + install) stays cached across almost every rebuild.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# Runtime — the image that actually ships.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY --from=builder --chown=app:app /build/.venv ./.venv
COPY --from=builder --chown=app:app /build/app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

# No HEALTHCHECK: readiness is answered by /v1/health/ready and owned by the
# orchestrator (Kubernetes probes, compose healthcheck), not baked into the
# image — the two have different retry/threshold semantics per environment.

# python -m uvicorn, not the `uvicorn` console-script: that script's shebang
# is baked with the interpreter path from wherever uv built the venv
# (/build/.venv in the builder stage) and breaks once the venv is copied to
# /app/.venv here. Invoking the module via the venv's actual python binary
# sidesteps the stale-shebang failure entirely.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
