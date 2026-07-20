# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

repo_root = Path.cwd()
static_dir = repo_root / "memesort_worker" / "web_static"
manifest_file = repo_root / "runtime-manifest.json"

# Bundle the immutable application files only. Large user data (the models,
# native runtime, development virtualenvs and the managed library) is downloaded
# and stored under %LOCALAPPDATA% at runtime, never packed into the executable.
datas = [
    (str(static_dir), "memesort_worker/web_static"),
    (str(manifest_file), "."),
]

# The native window backend (pywebview + WebView2 via pythonnet) resolves parts
# of its platform layer dynamically, so its submodules and data must be
# collected explicitly.
hiddenimports = list(collect_submodules("webview"))
hiddenimports += ["clr", "clr_loader"]

datas += collect_data_files("webview")

a = Analysis(
    [str(repo_root / "memesort_worker" / "desktop_entry.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="MemeSort",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MemeSort",
)
