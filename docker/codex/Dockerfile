# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG CODEX_VERSION=0.139.0
ARG CODEX_ACP_VERSION=0.16.0

ENV HOME=/home/codex \
    CODEX_HOME=/home/codex/.codex \
    CODEX_WORKSPACE=/app/repos \
    CODEX_ACP_PORT=3002 \
    XDG_CONFIG_HOME=/home/codex/.config \
    XDG_CACHE_HOME=/home/codex/.cache \
    XDG_STATE_HOME=/home/codex/.local/state

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

RUN useradd -m -u 1003 -d /home/codex -s /bin/bash codex \
    && install -d -o codex -g codex \
        "${CODEX_HOME}" \
        "${CODEX_WORKSPACE}" \
        "${XDG_CONFIG_HOME}" \
        "${XDG_CACHE_HOME}" \
        "${XDG_STATE_HOME}"

# Install Codex CLI and ACP adapter — optional deps bring the native binary
RUN npm install -g \
        "@openai/codex@${CODEX_VERSION}" \
        "@zed-industries/codex-acp@${CODEX_ACP_VERSION}" \
    && npm cache clean --force

COPY docker/codex/entrypoint.sh /usr/local/bin/codex-entrypoint
RUN chmod 0755 /usr/local/bin/codex-entrypoint

WORKDIR /app/repos

EXPOSE 3002

# Persist codex config/auth and the shared repo workspace
VOLUME ["/home/codex/.codex", "/app/repos"]

ENTRYPOINT ["/usr/local/bin/codex-entrypoint"]
