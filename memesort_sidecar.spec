# -*- mode: python ; coding: utf-8 -*-
"""Headless Python sidecar for the Tauri portable distribution.

This contains the headless application host only. Models, Vulkan runtime files,
and every mutable Library file remain outside the bundle under MemeSortData.
"""

from pathlib import Path


repo_root = Path(SPECPATH)
datas = [
    (str(repo_root / "memesort_worker" / "web_static"), "memesort_worker/web_static"),
    (str(repo_root / "runtime-manifest.json"), "."),
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
    name="memesort-sidecar-x86_64-pc-windows-msvc",
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
    name="memesort-sidecar-x86_64-pc-windows-msvc",
)
