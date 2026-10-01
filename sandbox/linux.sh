#!/usr/bin/env bash
# Run the scenario in a throwaway Linux container. The source is mounted read-only; nothing persists.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
docker run --rm -v "$here:/src:ro" python:3.13-slim bash -c '
  set -e
  apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null
  cp -r /src /tmp/bondi && pip install -q /tmp/bondi >/dev/null 2>&1
  export SANDBOX=$(mktemp -d) BONDI=bondi
  bash /tmp/bondi/sandbox/scenario.sh
'
