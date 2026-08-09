from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DATA_DIR_NAME = "MemeSortData"

# Environment overrides. Tests and packaging drive path resolution through these
# instead of assuming the source checkout is the product root.
ENV_APP_ROOT = "MEMESORT_APP_ROOT"
ENV_LIBRARY_ROOT = "MEMESORT_LIBRARY_ROOT"
ENV_PORTABLE_ROOT = "MEMESORT_PORTABLE_ROOT"

MANIFEST_FILENAME = "runtime-manifest.json"
_PACKAGE_DIR_NAME = "memesort_worker"
_STATIC_DIR_NAME = "web_static"


@dataclass(frozen=True)
class AppPaths:
    """Resolved location of the read-only application files for one session.

    Its job is to stop modules assuming ``Path(__file__).parents[...]`` is the
    product root, so static assets and the runtime manifest resolve correctly in
    a development checkout, a PyInstaller frozen build, and under test. The
    The portable root is the directory containing ``MemeSort.exe``. All mutable
    application data belongs beneath its ``MemeSortData`` directory, never the
    process current directory or a roaming user-profile location.
    """

    application_root: Path
    manifest_path: Path
    static_root: Path
    portable_root: Path
    data_root: Path
    default_library_root: Path
    models_root: Path
    runtime_root: Path

    @classmethod
    def discover(cls, env: Mapping[str, str] | None = None) -> "AppPaths":
        environ = os.environ if env is None else env
        application_root, static_root, manifest_path = _resolve_application_layout(environ)
        portable_root = _resolve_portable_root(application_root, environ)
        data_root = portable_root / DATA_DIR_NAME
        return cls(
            application_root=application_root,
            manifest_path=manifest_path,
            static_root=static_root,
            portable_root=portable_root,
            data_root=data_root,
            default_library_root=_resolve_default_library_root(data_root, environ),
            models_root=data_root / "models",
            runtime_root=data_root / "runtime",
        )


def _resolve_application_layout(env: Mapping[str, str]) -> tuple[Path, Path, Path]:
    override = env.get(ENV_APP_ROOT)
    if override:
        application_root = Path(override).expanduser().resolve()
        return (
            application_root,
            _static_root_for(application_root),
            application_root / MANIFEST_FILENAME,
        )

    if getattr(sys, "frozen", False):
        # PyInstaller onedir: bundled data lives under ``sys._MEIPASS`` while the
        # launcher executable defines the install directory.
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        application_root = Path(sys.executable).parent.resolve()
        static_root = bundle_root / _PACKAGE_DIR_NAME / _STATIC_DIR_NAME
        manifest_path = bundle_root / MANIFEST_FILENAME
        if not manifest_path.exists():
            manifest_path = application_root / MANIFEST_FILENAME
        return application_root, static_root, manifest_path

    package_dir = Path(__file__).resolve().parent
    application_root = package_dir.parent
    return (
        application_root,
        package_dir / _STATIC_DIR_NAME,
        application_root / MANIFEST_FILENAME,
    )


def _static_root_for(application_root: Path) -> Path:
    nested = application_root / _PACKAGE_DIR_NAME / _STATIC_DIR_NAME
    if nested.exists():
        return nested
    return application_root / _STATIC_DIR_NAME


def _resolve_portable_root(application_root: Path, env: Mapping[str, str]) -> Path:
    override = env.get(ENV_PORTABLE_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    return application_root


def _resolve_default_library_root(data_root: Path, env: Mapping[str, str]) -> Path:
    override = env.get(ENV_LIBRARY_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    return data_root / "library"
