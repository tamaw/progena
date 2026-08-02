#!/usr/bin/env bash
# Regenerates the self-signed TLS certs checked into app/frontend/certs/ and
# docker/backend/certs/ (see .gitignore's negated `!*.pem` rule for those two
# paths -- these are intentionally committed, not real secrets: LAN-only
# self-signed dev/deployment certs, not CA-issued).
#
# Only the frontend cert is ever validated by a browser: nginx.conf
# terminates TLS for every external request and proxies to the backend over
# the internal docker network with `proxy_ssl_verify off` (trusted
# implicitly, never browser-facing). So a deployment reached by anything
# other than localhost/127.0.0.1 -- a LAN IP, a mDNS/.local hostname, a real
# domain -- only needs that address added to the FRONTEND cert's SAN list for
# the browser to accept the connection. The backend cert is regenerated too
# for consistency, but its SAN list rarely needs the extra entries.
#
# Usage:
#   docker/generate-certs.sh [extra-san ...]
#   docker/generate-certs.sh IP:192.168.0.2 DNS:cronix
#
# Re-run (and rebuild + redeploy the frontend image) whenever a deployment's
# reachable address changes -- DHCP reassignment, new network, new hostname.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DAYS=3650
EXTRA_SANS=("$@")

generate_cert() {
  local common_name="$1" out_dir="$2"
  shift 2
  local base_sans=("$@")
  local all_sans=("${base_sans[@]}" "${EXTRA_SANS[@]}")
  local san_string
  san_string=$(IFS=,; echo "${all_sans[*]}")

  mkdir -p "$out_dir"
  openssl req -x509 -nodes -newkey rsa:2048 -days "$DAYS" \
    -keyout "$out_dir/key.pem" -out "$out_dir/cert.pem" \
    -subj "/CN=$common_name" \
    -addext "subjectAltName=$san_string" >/dev/null 2>&1
  echo "generated $out_dir/cert.pem (CN=$common_name, SAN=$san_string)"
}

generate_cert progenitor-frontend app/frontend/certs \
  DNS:localhost DNS:progenitor-frontend DNS:progenitor IP:127.0.0.1

generate_cert progenitor-backend docker/backend/certs \
  DNS:localhost DNS:progenitor-backend IP:127.0.0.1
