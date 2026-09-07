#!/usr/bin/env bash
# Ghost — dev launcher (run without installing).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$HERE/src/config-fast.yaml"
if [[ -f "$HERE/config.local.yaml" ]]; then
  CONFIG="$HERE/config.local.yaml"
fi
# An explicit --config in "$@" remains the final argparse choice.
# No shell sourcing or ambient CLI configuration is imported here.
exec python3 "$HERE/src/main.py" --config "$CONFIG" "$@"
