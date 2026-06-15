# syntax=docker/dockerfile:1.7
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG COPILOT_VERSION=1.0.62
ARG TARGETARCH

ENV HOME=/home/copilot \
    COPILOT_HOME=/home/copilot/.copilot \
    COPILOT_WORKSPACE=/app/repos \
    COPILOT_ACP_PORT=3000 \
    COPILOT_ACP_INTERNAL_PORT=3001 \
    XDG_CONFIG_HOME=/home/copilot/.config \
    XDG_CACHE_HOME=/home/copilot/.cache \
    XDG_STATE_HOME=/home/copilot/.local/state

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

RUN useradd -m -u 1001 -d /home/copilot -s /bin/bash copilot \
    && install -d -o copilot -g copilot "${COPILOT_HOME}" "${COPILOT_WORKSPACE}" "${XDG_CONFIG_HOME}" "${XDG_CACHE_HOME}" "${XDG_STATE_HOME}"

RUN case "${TARGETARCH:-amd64}" in \
        amd64) copilot_arch="x64" ;; \
        arm64) copilot_arch="arm64" ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/copilot.tgz "https://github.com/github/copilot-cli/releases/download/v${COPILOT_VERSION}/copilot-linux-${copilot_arch}.tar.gz" \
    && tar -xzf /tmp/copilot.tgz -C /usr/local/bin copilot \
    && chmod 0755 /usr/local/bin/copilot \
    && rm -f /tmp/copilot.tgz

COPY docker/ghcopilot/entrypoint.sh /usr/local/bin/ghcopilot-entrypoint
RUN chmod 0755 /usr/local/bin/ghcopilot-entrypoint

WORKDIR /app/repos

EXPOSE 3000

VOLUME ["/home/copilot/.copilot", "/app/repos"]

ENTRYPOINT ["/usr/local/bin/ghcopilot-entrypoint"]
