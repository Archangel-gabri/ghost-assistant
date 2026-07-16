#!/usr/bin/env bash
# Ghost — dev launcher (run without installing).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/src/main.py" --config "$HERE/src/config-fast.yaml" "$@"
