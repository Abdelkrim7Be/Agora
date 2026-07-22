#!/bin/sh
set -eu

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
GITLEAKS_IMAGE="zricethezav/gitleaks:v${GITLEAKS_VERSION}"

docker run --rm \
  -v "${ROOT}:/repo:ro" \
  "$GITLEAKS_IMAGE" \
  detect --source /repo --config /repo/.gitleaks.toml --redact --exit-code 1
