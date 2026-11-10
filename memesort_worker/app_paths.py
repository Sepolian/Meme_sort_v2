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
ENV_DATA_ROOT = "MEMESORT_DATA_ROOT"

MANIFEST_FILENAME = "runtime-manifest.json"


@dataclass(frozen=True)
class AppPaths:
    """Resolved location of the read-only application files for one session.

    Its job is to stop modules assuming ``Path(__file__).parents[...]`` is the
    product root, so the runtime manifest resolves correctly in a
    development checkout, a PyInstaller frozen build, and under test. The
    Windows portable root contains ``MemeSort.exe`` and ``MemeSortData``.
    macOS keeps mutable data in Application Support, separately from bundled
    resources. Neither layout depends on the process current directory.
    """

    application_root: Path
    manifest_path: Path
    portable_root: Path
    data_root: Path
    default_library_root: Path
    models_root: Path
    runtime_root: Path

    @classmethod
    def discover(cls, env: Mapping[str, str] | None = None) -> "AppPaths":
        environ = os.environ if env is None else env
        application_root, manifest_path = _resolve_application_layout(environ)
        portable_root = _resolve_portable_root(application_root, environ)
        if environ.get(ENV_DATA_ROOT):
            data_root = Path(environ[ENV_DATA_ROOT]).expanduser().resolve()
        elif sys.platform == "darwin" and not environ.get(ENV_PORTABLE_ROOT):
            data_root = (Path.home() / "Library" / "Application Support" / "MemeSort").resolve()
        else:
            data_root = portable_root / DATA_DIR_NAME
        return cls(
            application_root=application_root,
            manifest_path=manifest_path,
            portable_root=portable_root,
            data_root=data_root,
            default_library_root=_resolve_default_library_root(data_root, environ),
            models_root=data_root / "models",
            runtime_root=data_root / "runtime",
        )


def _resolve_application_layout(env: Mapping[str, str]) -> tuple[Path, Path]:
    override = env.get(ENV_APP_ROOT)
    if override:
        application_root = Path(override).expanduser().resolve()
        return application_root, application_root / MANIFEST_FILENAME

    if getattr(sys, "frozen", False):
        # PyInstaller onedir: bundled data lives under ``sys._MEIPASS`` while the
        # sidecar executable defines the install directory.
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        application_root = Path(sys.executable).parent.resolve()
        manifest_path = bundle_root / MANIFEST_FILENAME
        if not manifest_path.exists():
            manifest_path = application_root / MANIFEST_FILENAME
        return application_root, manifest_path

    package_dir = Path(__file__).resolve().parent
    application_root = package_dir.parent
    return application_root, application_root / MANIFEST_FILENAME


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
