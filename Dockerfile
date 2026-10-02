# MYIA server image — same core as the desktop/CLI form, packaged for 24/7
# self-hosting. Build context is the repository root:
#
#   docker build -t myia:local .
#
# Runtime entrypoint is the `myia` CLI; docker-compose overrides the command
# with `run <plugin.yaml> --loop` (see docker/docker-compose.yml).
#
# Build strategy: multi-stage with uv. The builder resolves the exact locked
# dependency set (uv.lock, --frozen) into a standalone .venv; the runtime
# stage copies that venv plus the source and drops to a non-root user.
# No credential value is ever baked into this image — everything flows in at
# runtime via environment variables referenced by plugin YAMLs (env:VAR).

# ---------------------------------------------------------------------------
# Stage 1: builder — resolve locked deps + install the myia package
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

# uv is copied from the official image instead of installed via pip/curl:
# no extra network round-trip, no shell installer. Pin the tag for
# reproducible builds when adopting a released version.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# UV_PYTHON_DOWNLOADS=never: use the python:3.11-slim interpreter, never a
# uv-managed download (keeps the venv ABI-consistent with the runtime stage).
# UV_COMPILE_BYTECODE=1: precompile .pyc at build time (faster cold start).
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# README.md is part of the hatchling build (pyproject readme = "README.md").
COPY pyproject.toml uv.lock README.md ./

# Workspace sources must exist before any uv sync: uv.lock references the
# myia-classifier member as an editable path (myia-classifier/), and
# --frozen resolution fails with "Distribution not found" without it.
# Trade-off: source changes now bust the dependency cache layer.
COPY src ./src
COPY myia-classifier ./myia-classifier

# Dependency layer: cached unless pyproject/uv.lock/sources change.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Full sync installs the myia package itself (editable by default; the source
# ships alongside the venv so that is fine in the final image too).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim base + tzdata, non-root, venv on PATH
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# tzdata is required for zoneinfo: plugin YAMLs carry IANA timezones
# (e.g. schedule.timezone: Asia/Shanghai) and APScheduler/zoneinfo resolve
# them against the OS zone database. python:*-slim ships without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; uid 1000 must own the mounted data directory
# (docker/README.md — `chown -R 1000:1000 data` or override MYIA_UID/GID).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin myia

COPY --from=builder --chown=myia:myia /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER myia

# Health dashboard / web panel is a later milestone (plan: 旁观窗口);
# this image only carries the pipeline core + CLI.
LABEL org.opencontainers.image.title="MYIA" \
      org.opencontainers.image.description="AI-native intelligence hub — config-driven fetch, classify, dedup, push" \
      org.opencontainers.image.source="https://github.com/xinzhuzi/MYIA" \
      org.opencontainers.image.licenses="MIT"

# `docker run <image>` prints the version; the real workload is
# `myia run <yaml> --loop`, set by docker-compose (or by hand).
ENTRYPOINT ["myia"]
CMD ["--version"]
