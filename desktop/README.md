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

The application is intentionally Windows x64-only until another platform is
explicitly validated.
