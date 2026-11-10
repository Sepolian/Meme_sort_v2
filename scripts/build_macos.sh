#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Build on an Apple Silicon Mac with Xcode Command Line Tools installed." >&2
  exit 1
fi
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
uv_bin="$(command -v uv || true)"
if [[ -z "$uv_bin" ]]; then uv_bin="$HOME/.local/bin/uv"; fi
export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo >/dev/null
command -v npm >/dev/null
"$uv_bin" sync --locked --group build --python 3.13
python_bin="$repo_root/.venv/bin/python"
"$python_bin" -c 'import platform; assert platform.machine() == "arm64", "Use an arm64 Python build environment"'

# npm ci must never follow a shared dependency symlink into another checkout.
if [[ -L desktop/node_modules ]]; then unlink desktop/node_modules; fi
(cd desktop && npm ci)
"$python_bin" -m PyInstaller --noconfirm --clean \
  --workpath "$repo_root/build/macos-sidecar" \
  --distpath "$repo_root/dist/macos-sidecar" \
  "$repo_root/memesort_sidecar.spec"
stage="$repo_root/desktop/src-tauri/resources/sidecar"
rm -rf -- "$stage"
mkdir -p "$stage"
printf '*\n!.gitignore\n' > "$stage/.gitignore"
ditto "$repo_root/dist/macos-sidecar/memesort-sidecar-aarch64-apple-darwin" "$stage"
/usr/bin/lipo -verify_arch arm64 "$stage/memesort-sidecar-aarch64-apple-darwin"
(cd desktop && npm run tauri -- build --target aarch64-apple-darwin --bundles app)
target_root="${CARGO_TARGET_DIR:-$repo_root/desktop/src-tauri/target}"
app="$target_root/aarch64-apple-darwin/release/bundle/macos/MemeSort.app"
"$python_bin" "$repo_root/scripts/test_macos_package.py" "$app"
printf 'Built Mac app: %s\n' "$app"
