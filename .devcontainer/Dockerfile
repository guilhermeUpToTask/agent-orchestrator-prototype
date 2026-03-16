# =============================================================================
# .devcontainer/Dockerfile
#
# Development environment for agent-orchestrator.
# This is the only Dockerfile — the project runs locally, not as a deployed
# service. Redis is installed as a local process for event-driven architecture.
#
# Includes:
#   - Python 3.11
#   - redis-server (process local, no sidecar)
#   - Node.js 20 + pi CLI
#   - git (workspace adapter clones/commits real repos)
#   - uv (fast Python package installer)
# =============================================================================

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        redis-server \
        redis-tools \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 LTS — for pi CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Agent CLIs
RUN npm install -g @mariozechner/pi-coding-agent 2>/dev/null || true

# uv — install via official installer (no pip bootstrap)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.cargo/bin/uv /usr/local/bin/uv 2>/dev/null \
    || mv /root/.local/bin/uv /usr/local/bin/uv

# Redis dev config: local only, no persistence, no auth
RUN printf "bind 127.0.0.1\nsave ''\nloglevel warning\nprotected-mode no\n" \
    > /etc/redis/redis-dev.conf

WORKDIR /workspaces/agent-orchestrator