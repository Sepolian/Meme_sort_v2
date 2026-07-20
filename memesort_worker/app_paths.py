from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APP_DIR_NAME = "MemeSort"

# Environment overrides. Tests and packaging drive path resolution through these
# instead of assuming the source checkout is the product root.
ENV_APP_ROOT = "MEMESORT_APP_ROOT"
ENV_LIBRARY_ROOT = "MEMESORT_LIBRARY_ROOT"

MANIFEST_FILENAME = "runtime-manifest.json"
_PACKAGE_DIR_NAME = "memesort_worker"
_STATIC_DIR_NAME = "web_static"


@dataclass(frozen=True)
class AppPaths:
    """Resolved location of the read-only application files for one session.

    Its job is to stop modules assuming ``Path(__file__).parents[...]`` is the
    product root, so static assets and the runtime manifest resolve correctly in
    a development checkout, a PyInstaller frozen build, and under test. The
    runtime and models keep installing where ``runtime-manifest.json`` places
    them (repo-relative ``.runtime`` / ``.models``); this type does not relocate
    them.
    """

    application_root: Path
    manifest_path: Path
    static_root: Path
    default_library_root: Path

    @classmethod
    def discover(cls, env: Mapping[str, str] | None = None) -> "AppPaths":
        environ = os.environ if env is None else env
        application_root, static_root, manifest_path = _resolve_application_layout(environ)
        return cls(
            application_root=application_root,
            manifest_path=manifest_path,
            static_root=static_root,
            default_library_root=_resolve_default_library_root(environ),
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


def _resolve_default_library_root(env: Mapping[str, str]) -> Path:
    override = env.get(ENV_LIBRARY_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    # Preserve the historical roaming location so existing libraries keep working
    # without a silent move.
    appdata = env.get("APPDATA")
    if appdata:
        return (Path(appdata) / APP_DIR_NAME).resolve()
    return (Path.home() / "AppData" / "Roaming" / APP_DIR_NAME).resolve()
