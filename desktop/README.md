# MemeSort desktop

This directory contains the Windows portable and Apple Silicon macOS Tauri 2 desktop host. Tauri owns the
managed Python `LocalAppHost` sidecar lifecycle, consumes its private bootstrap
session, and proxies only fixed backend commands and managed media to the
WebView. The WebView must never call the Python loopback API directly.

## Development

From this directory:

```powershell
npm install
npm run tauri dev
```

## Verification

```powershell
npm run lint
npm run typecheck
npm test
npm run build
npm run rust:fmt
npm run rust:clippy
npm run rust:test
```

`npm run build` produces WebView assets in `desktop/dist/`; it does not
produce a desktop executable. The Windows x64 [portable build](../scripts/build_portable.ps1)
runs Tauri in release mode and embeds that frontend in `MemeSort.exe`.
See the [packaging instructions](../README.md#portable-desktop-package)
for the ZIP, runtime setup, and prerequisites.

The [portable smoke test](../scripts/test_portable_smoke.ps1) checks the
staged files and starts the headless sidecar. It does not open the WebView.
To check a UI release, extract the ZIP to a fresh folder and launch
`MemeSort.exe` with the dev server stopped. Verify the empty Library and
its illustration, import a still and a GIF by picker and drop, then check
thumbnails, inspector, Clipboard Copy, and the saved theme after relaunch.

## Apple Silicon Mac package

Build on an Apple Silicon Mac with Xcode Command Line Tools, Rust/Cargo, Node/npm,
and uv installed. From the repository root:

```sh
bash scripts/build_macos.sh
```

The script installs the locked Python 3.13 build group and npm dependencies,
builds the complete PyInstaller sidecar, stages its environment into Tauri
resources, and produces an ad-hoc signed arm64 `MemeSort.app`. The default output
is `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/MemeSort.app`
(or the same target-relative path under `CARGO_TARGET_DIR`). Normal app launch
uses the bundled Python environment and needs no developer Python, Node, or Rust.
Models, the semantic runtime, and the separate CPU OCR environment are installed
by the bundled one-time `setup_macos_runtime.sh` script; they are not included
in this app bundle. Before setup, the Library opens with **Setup needed**.

Mac data stays under `~/Library/Application Support/MemeSort`: `library/`,
`models/`, and `runtime/`; lifecycle and Library logs are in `library/logs/`.
Moving/replacing the app does not move or delete this directory. The host closes
its sole window by quitting the app and stopping the sidecar; Command-Q uses the
normal native application menu. Windows keeps its existing portable layout.

The frozen sidecar lives at
`Contents/Resources/sidecar/memesort-sidecar-aarch64-apple-darwin`, beside its
complete `_internal/` environment. Python resolves the manifest and packaged
`scripts/paddle_ocr_worker.py` from PyInstaller resources. Tauri passes
`--data-root <absolute directory>`, which sets `MEMESORT_DATA_ROOT` throughout the
session; runtime/model paths are rehomed under that directory. The existing
`--portable-root` / `MEMESORT_PORTABLE_ROOT` contract remains the Windows contract.
`MEMESORT_DATA_ROOT` can isolate a development/acceptance Library; it is not an
in-app location preference. The Mac OCR environment belongs in
`runtime/ocr-venv/bin/python`, with its model cache in `models/paddleocr`.

The build ends with the reproducible package check. It can also be run alone:

```sh
.venv/bin/python scripts/test_macos_package.py /absolute/path/to/MemeSort.app
```

This verifies arm64 and deep code signatures, launches the actual frozen sidecar
with a minimal PATH outside the checkout, imports a still through authenticated
HTTP, reads its managed media, deletes its Source Path, moves the app, and checks
persistent Library access and clean shutdown/parent EOF. Data and copied apps are
isolated in a temporary directory. It does not open a WebView or verify native
window focus, close/Command-Q, Finder/Dock launch, single-instance activation,
Finder paste, OCR, or Metal inference. Those require the post-integration native
acceptance run; no native workflow success is implied by the smoke check.
