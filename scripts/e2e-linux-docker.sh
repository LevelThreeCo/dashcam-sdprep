#!/bin/bash
# Run the Linux end-to-end test in a privileged Debian container (works from macOS too).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
docker run --rm --privileged -v "$REPO:/repo:ro" debian:bookworm bash -c '
  apt-get update -qq >/dev/null && apt-get install -y -qq python3 dosfstools fdisk util-linux >/dev/null 2>&1
  /repo/scripts/e2e-linux.sh'
