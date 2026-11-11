#!/bin/bash
set -euo pipefail
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Run setup natively on an Apple Silicon Mac; Rosetta is unsupported." >&2
  exit 1
fi
script_dir="$(cd "$(dirname "$0")" && pwd)"
sidecar="$script_dir/memesort-sidecar-aarch64-apple-darwin"
if [[ -x "$sidecar" ]]; then
  exec "$sidecar" --setup-macos-runtime "$@"
fi
# Checkout use only; installed apps always use the bundled frozen sidecar above.
repo_root="$(cd "$script_dir/.." && pwd)"
if [[ -f "$repo_root/runtime-manifest.json" ]]; then
  export PYTHONPATH="$repo_root"
  exec "${MEMESORT_SETUP_PYTHON:-$repo_root/.venv/bin/python3}" -m memesort_worker.macos_setup "$@"
fi
echo "Setup resources are incomplete. Replace MemeSort.app and rerun its bundled setup script." >&2
exit 1
