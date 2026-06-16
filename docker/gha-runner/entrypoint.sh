#!/usr/bin/env bash
set -euo pipefail

RUNNER_HOME="${RUNNER_HOME:-/home/runner/actions-runner}"
RUNNER_WORKDIR="${RUNNER_WORKDIR:-/home/runner/_work}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,Linux,python,node,docker}"
RUNNER_NAME_PREFIX="${RUNNER_NAME_PREFIX:-gha-runner}"
RUNNER_SCOPE="${RUNNER_SCOPE:-repo}"
RUNNER_EPHEMERAL="${RUNNER_EPHEMERAL:-false}"
RUNNER_REPLACE="${RUNNER_REPLACE:-true}"
RUNNER_USER_HOME="/home/runner"

run_as_runner() {
    sudo -H -E -u runner env HOME="${RUNNER_USER_HOME}" USER=runner LOGNAME=runner "$@"
}

prune_stale_venvs() {
    find /home/runner/_probe/_work -path '*/.venv/bin/python' -type l 2>/dev/null | while read -r python_link; do
        python_target="$(readlink "$python_link" || true)"
        if [[ -z "$python_target" ]]; then
            continue
        fi
        if [[ "$python_target" == /home/runner/_probe/_work/_tool/* ]] && [[ ! -e "$python_link" ]]; then
            rm -rf "$(dirname "$(dirname "$python_link")")"
        fi
    done
}

if [[ -S /var/run/docker.sock ]]; then
    docker_gid="$(stat -c '%g' /var/run/docker.sock)"
    if getent group "${docker_gid}" >/dev/null 2>&1; then
        docker_group="$(getent group "${docker_gid}" | cut -d: -f1)"
    elif getent group docker >/dev/null 2>&1; then
        groupmod -g "${docker_gid}" docker
        docker_group="docker"
    else
        groupadd -g "${docker_gid}" docker
        docker_group="docker"
    fi
    usermod -aG "${docker_group}" runner
fi

mkdir -p "${RUNNER_WORKDIR}"
chown -R runner:runner /home/runner
prune_stale_venvs

runner_name="${RUNNER_NAME:-${RUNNER_NAME_PREFIX}-$(hostname)}"
config_args=(
    --unattended
    --name "${runner_name}"
    --work "${RUNNER_WORKDIR}"
    --labels "${RUNNER_LABELS}"
)

if [[ "${RUNNER_REPLACE}" == "true" ]]; then
    config_args+=(--replace)
fi

if [[ "${RUNNER_EPHEMERAL}" == "true" ]]; then
    config_args+=(--ephemeral)
fi

if [[ -n "${RUNNER_GROUP:-}" ]]; then
    config_args+=(--runnergroup "${RUNNER_GROUP}")
fi

if [[ ! -f "${RUNNER_HOME}/.runner" ]]; then
    : "${RUNNER_URL:?RUNNER_URL is required}"
    : "${RUNNER_TOKEN:?RUNNER_TOKEN is required}"

    config_args+=(--url "${RUNNER_URL}" --token "${RUNNER_TOKEN}")
    if [[ "${RUNNER_SCOPE}" == "org" ]]; then
        config_args+=(--disableupdate)
    fi

    run_as_runner "${RUNNER_HOME}/config.sh" "${config_args[@]}"
fi

cleanup() {
    if [[ "${REMOVE_RUNNER_ON_EXIT:-true}" != "true" ]]; then
        return
    fi
    if [[ ! -f "${RUNNER_HOME}/.runner" ]]; then
        return
    fi
    if [[ -z "${RUNNER_TOKEN:-}" ]]; then
        return
    fi
    run_as_runner "${RUNNER_HOME}/config.sh" remove --unattended --token "${RUNNER_TOKEN}" || true
}

trap cleanup EXIT INT TERM

exec sudo -H -E -u runner env HOME="${RUNNER_USER_HOME}" USER=runner LOGNAME=runner "${RUNNER_HOME}/run.sh"
