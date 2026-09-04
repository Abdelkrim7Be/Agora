#!/bin/sh
set -eu

TRIVY_VERSION="${TRIVY_VERSION:-0.70.0}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CACHE_DIR="${TRIVY_CACHE_DIR:-${ROOT}/.trivy-cache}"
TRIVY_IMAGE="aquasec/trivy:${TRIVY_VERSION}"

mkdir -p "$CACHE_DIR"

run_trivy() {
  docker run --rm \
    -v "${ROOT}:/repo:ro" \
    -v "${CACHE_DIR}:/root/.cache/" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$TRIVY_IMAGE" "$@"
}

scan_image() {
  image="$1"
  context="$2"

  # email-agent's Dockerfile COPYs platform-core/ as a sibling, so it needs
  # the parent `services` dir as build context — a service-local context
  # can't see that directory at all.
  if [ "$image" = "email-agent" ]; then
    docker build --pull -t "agora-${image}:ci" -f "${ROOT}/services/${context}/Dockerfile" "${ROOT}/services"
  else
    docker build --pull -t "agora-${image}:ci" "${ROOT}/services/${context}"
  fi
  run_trivy image \
    --scanners vuln \
    --pkg-types os,library \
    --severity HIGH,CRITICAL \
    --ignore-unfixed \
    --exit-code 1 \
    "agora-${image}:ci"
}

run_trivy fs \
  --scanners vuln \
  --pkg-types library \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --skip-dirs /repo/.trivy-cache \
  --skip-dirs /repo/node_modules \
  --skip-dirs /repo/services/email-agent/.venv \
  --skip-dirs /repo/services/security/.venv \
  --skip-dirs /repo/agent-IA-ETHIK-PORTAGE- \
  --exit-code 1 \
  /repo

scan_image email-agent email-agent
scan_image security security
scan_image gateway gateway
scan_image web-react web-react
