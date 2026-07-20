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
ENV_MUTABLE_ROOT = "MEMESORT_MUTABLE_ROOT"
ENV_LIBRARY_ROOT = "MEMESORT_LIBRARY_ROOT"

MANIFEST_FILENAME = "runtime-manifest.json"
_PACKAGE_DIR_NAME = "memesort_worker"
_STATIC_DIR_NAME = "web_static"


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem layout for one MemeSort session.

    ``application_root`` and everything derived from it points at read-only
    installed files.  ``mutable_root`` and its children hold runtime, models,
    logs and (optionally) the managed library, and are the only locations the
    application writes to.  The distinction lets a per-user installer keep the
    program directory read-only while user data lives under
    ``%LOCALAPPDATA%``.
    """

    application_root: Path
    manifest_path: Path
    static_root: Path
    mutable_root: Path
    runtime_root: Path
    models_root: Path
    logs_root: Path
    default_library_root: Path

    @classmethod
    def discover(cls, env: Mapping[str, str] | None = None) -> "AppPaths":
        environ = os.environ if env is None else env
        application_root, static_root, manifest_path = _resolve_application_layout(environ)
        mutable_root = _resolve_mutable_root(environ)
        return cls(
            application_root=application_root,
            manifest_path=manifest_path,
            static_root=static_root,
            mutable_root=mutable_root,
            runtime_root=mutable_root / "runtime",
            models_root=mutable_root / "models",
            logs_root=mutable_root / "logs",
            default_library_root=_resolve_default_library_root(environ),
        )

    def ensure_mutable_tree(self) -> None:
        """Create the writable directories this session owns."""
        for path in (self.mutable_root, self.runtime_root, self.models_root, self.logs_root):
            path.mkdir(parents=True, exist_ok=True)


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


def _resolve_mutable_root(env: Mapping[str, str]) -> Path:
    override = env.get(ENV_MUTABLE_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    local_appdata = env.get("LOCALAPPDATA")
    if local_appdata:
        return (Path(local_appdata) / APP_DIR_NAME).resolve()
    return (Path.home() / "AppData" / "Local" / APP_DIR_NAME).resolve()


def _resolve_default_library_root(env: Mapping[str, str]) -> Path:
    override = env.get(ENV_LIBRARY_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    # Preserve the historical roaming location so existing libraries keep working
    # without a silent move.  Migration to the mutable root is an explicit,
    # opt-in step, never a side effect of discovery.
    appdata = env.get("APPDATA")
    if appdata:
        return (Path(appdata) / APP_DIR_NAME).resolve()
    return (Path.home() / "AppData" / "Roaming" / APP_DIR_NAME).resolve()
