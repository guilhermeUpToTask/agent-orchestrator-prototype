# =============================================================================
# .devcontainer/Dockerfile
#
# Development environment for agent-orchestrator.
#
# Includes:
#   - Python 3.12 (matches CI)
#   - Redis
#   - Node.js 22 LTS
#   - Claude Code, Pi Coding Agent, Codex CLI, Grok CLI, mimo CLI
#   - gh CLI, bubblewrap
#   - uv
#   - Git + common developer tooling
# =============================================================================

FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

# -----------------------------------------------------------------------------
# Base packages
#
# gh and bubblewrap come straight from the Debian repos already configured
# in this base image (no extra apt source needed).
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
        gh \
        bubblewrap \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Node.js 22 LTS
# -----------------------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Agent CLIs (npm-distributed)
# -----------------------------------------------------------------------------
RUN npm install -g \
        @mariozechner/pi-coding-agent \
        @anthropic-ai/claude-code

# -----------------------------------------------------------------------------
# Agent CLIs (vendor install scripts, pinned to the versions verified against
# .orchestrator/runtime-pool.yaml) -- these were previously ad-hoc installs
# made directly in the running container and lost on every rebuild.
# -----------------------------------------------------------------------------
RUN curl -fsSL https://chatgpt.com/codex/install.sh > /tmp/codex-install.sh \
    && CODEX_NON_INTERACTIVE=1 sh /tmp/codex-install.sh --release 0.144.5 \
    && rm /tmp/codex-install.sh

RUN curl -fsSL https://mimo.xiaomi.com/install > /tmp/mimo-install.sh \
    && bash /tmp/mimo-install.sh --version 0.1.6 \
    && rm /tmp/mimo-install.sh

RUN curl -fsSL https://x.ai/cli/install.sh > /tmp/grok-install.sh \
    && bash /tmp/grok-install.sh 0.2.102 \
    && rm /tmp/grok-install.sh

ENV PATH="/root/.local/bin:/root/.grok/bin:/root/.mimocode/bin:${PATH}"

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
    && gh --version \
    && bwrap --version \
    && claude --version || true \
    && pi --version || true \
    && codex --version || true \
    && mimo --version || true \
    && grok --version || true

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