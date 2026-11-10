# MemeSort desktop

This directory contains the Windows-first Tauri 2 desktop host. Tauri owns the
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
The current packaging workflow produces no macOS `.app`.
