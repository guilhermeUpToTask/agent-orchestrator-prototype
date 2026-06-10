# =============================================================================
# .devcontainer/Dockerfile
#
# Development environment for agent-orchestrator.
#
# Includes:
#   - Python 3.11
#   - Redis
#   - Node.js 22 LTS
#   - Claude Code
#   - Pi Coding Agent
#   - uv
#   - Git + common developer tooling
# =============================================================================

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# -----------------------------------------------------------------------------
# Base packages
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        redis-server \
        redis-tools \
        curl \
        procps \
        bash \
        jq \
        less \
        ripgrep \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Node.js 22 LTS
# -----------------------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Agent CLIs
# -----------------------------------------------------------------------------
RUN npm install -g \
        @mariozechner/pi-coding-agent \
        @anthropic-ai/claude-code

# -----------------------------------------------------------------------------
# uv
# -----------------------------------------------------------------------------
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && (mv /root/.cargo/bin/uv /usr/local/bin/uv 2>/dev/null \
        || mv /root/.local/bin/uv /usr/local/bin/uv)

# -----------------------------------------------------------------------------
# Redis dev config
# -----------------------------------------------------------------------------
RUN printf "bind 127.0.0.1\nsave ''\nloglevel warning\nprotected-mode no\n" \
    > /etc/redis/redis-dev.conf

# -----------------------------------------------------------------------------
# Verification
# -----------------------------------------------------------------------------
RUN python --version \
    && node --version \
    && npm --version \
    && uv --version \
    && git --version \
    && claude --version || true \
    && pi --version || true

# -----------------------------------------------------------------------------
# Workspace
# -----------------------------------------------------------------------------
WORKDIR /workspaces/agent-orchestrator

# -----------------------------------------------------------------------------
# Python defaults
# -----------------------------------------------------------------------------
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["/bin/bash"]