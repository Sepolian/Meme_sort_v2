# -*- mode: python ; coding: utf-8 -*-
"""Headless Python sidecar for the Tauri portable distribution.

This contains the headless application host only. Models, Vulkan runtime files,
and every mutable Library file remain outside the bundle in platform data directories.
"""

import sys
from pathlib import Path


repo_root = Path(SPECPATH)
sidecar_name = "memesort-sidecar-aarch64-apple-darwin" if sys.platform == "darwin" else "memesort-sidecar-x86_64-pc-windows-msvc"
datas = [
    (str(repo_root / "runtime-manifest.json"), "."),
    (str(repo_root / "scripts" / "paddle_ocr_worker.py"), "scripts"),
    (str(repo_root / "requirements-ocr.txt"), "."),
]

a = Analysis(
    [str(repo_root / "memesort_sidecar_entry.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=sidecar_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=sidecar_name,
)
