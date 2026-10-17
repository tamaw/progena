# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG CLAUDE_ACP_VERSION=0.93.0

ENV HOME=/home/claude \
    CLAUDE_CONFIG_DIR=/home/claude/.claude \
    CLAUDE_WORKSPACE=/app/repos \
    CLAUDE_ACP_PORT=3001 \
    XDG_CONFIG_HOME=/home/claude/.config \
    XDG_CACHE_HOME=/home/claude/.cache \
    XDG_STATE_HOME=/home/claude/.local/state

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        less \
        openssh-client \
        procps \
        ripgrep \
        socat \
    && rm -rf /var/lib/apt/lists/*

# UID 10001 matches the Progenitor runtime, which shares the /app/repos workspace.
RUN useradd -m -u 10001 -d /home/claude -s /bin/bash claude \
    && install -d -o claude -g claude \
        "${CLAUDE_CONFIG_DIR}" \
        "${CLAUDE_WORKSPACE}" \
        "${XDG_CONFIG_HOME}" \
        "${XDG_CACHE_HOME}" \
        "${XDG_STATE_HOME}"

RUN npm install -g "@agentclientprotocol/claude-agent-acp@${CLAUDE_ACP_VERSION}" \
    && npm cache clean --force

COPY docker/claude/entrypoint.sh /usr/local/bin/claude-entrypoint
RUN chmod 0755 /usr/local/bin/claude-entrypoint

WORKDIR /app/repos

EXPOSE 3001

VOLUME ["/home/claude/.claude", "/app/repos"]

ENTRYPOINT ["/usr/local/bin/claude-entrypoint"]
