"""Verified semantic installer shared by the frozen sidecar and checkout launcher.

Ticket05 extends run_setup() with final-location CPU OCR provisioning. Semantic
activation alone never claims OCR readiness or authorizes an application session.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

from .app_paths import AppPaths
from .llama_cpp_backend import probe_llama_devices
from .runtime_activation import write_runtime_activation
from .runtime_admission import crosscheck_llama_mtl0, validate_macos_arm64
from .runtime_manifest import ArtifactSpec, RuntimeManifest, load_runtime_manifest, resolve_runtime_target


def verify_artifact(path: Path, spec: ArtifactSpec) -> None:
    if not path.is_file() or path.stat().st_size != spec.size_bytes:
        raise ValueError(f"Artifact verification failed (size/missing): {path}. Rerun setup with network access.")
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != spec.sha256:
        raise ValueError(f"Artifact verification failed (SHA256): {path}. Rerun setup with network access.")


def extract_archive(archive: Path, destination: Path) -> Path:
    """Allow internal dylib symlinks, reject escapes/special files before extraction."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        wrappers = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
        if len(wrappers) != 1:
            raise ValueError("Expected one archive wrapper directory")
        wrapper = next(iter(wrappers))
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError(f"Unsafe archive member: {member.name}")
            if member.issym():
                link = PurePosixPath(member.linkname)
                resolved = PurePosixPath(os.path.normpath(str(path.parent / link)))
                if link.is_absolute() or not resolved.parts or resolved.parts[0] != wrapper:
                    raise ValueError(f"Unsafe archive link: {member.name} -> {member.linkname}")
            # data_filter also validates link destinations and paths through earlier links.
            tarfile.data_filter(member, str(destination))
        tar.extractall(destination, members=members, filter='data')
    children = list(destination.iterdir())
    if len(children) != 1 or not children[0].is_dir() or children[0].is_symlink():
        raise ValueError(f"Expected one archive wrapper directory: {archive}")
    return children[0]


def _download(spec: ArtifactSpec, cache: Path, offline: bool) -> Path:
    path = cache / spec.filename
    if path.exists():
        try:
            verify_artifact(path, spec)
            return path
        except ValueError:
            if offline:
                raise
    if offline:
        raise ValueError(f"Artifact verification failed: offline cache is missing {path}")
    cache.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    try:
        with urlopen(spec.url, timeout=300) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        verify_artifact(temporary, spec)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def install_semantic_runtime(manifest: RuntimeManifest, *, downloads: Path | None = None, offline: bool = False) -> dict:
    started = time.monotonic()
    if manifest.platform.os != 'macos' or manifest.platform.device != 'MTL0':
        raise ValueError('Mac setup requires the explicit macos-arm64 MTL0 target.')
    cache = downloads or manifest.download_dir
    runtime_root = manifest.activation_record_path.parent
    runtime_root.mkdir(parents=True, exist_ok=True)
    with (runtime_root / 'setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another runtime setup is running.') from exc
        for path in (manifest.llama_server_path, manifest.main_model_path, manifest.projector_path):
            if path.exists():
                result = subprocess.run(['/usr/sbin/lsof', '-t', str(path)], capture_output=True, text=True)
                if result.returncode == 0:
                    raise ValueError(f'Close MemeSort and its runtime before setup: {path}')
                if result.returncode != 1:
                    raise ValueError(f'Could not check whether runtime files are in use: {result.stderr}')
        # Download and stage everything before touching an existing installation.
        specs = (manifest.toolchain.uv.archive, manifest.llama_cpp.archive, manifest.model.main, manifest.model.projector)
        artifacts = [_download(spec, cache, offline) for spec in specs]
        with tempfile.TemporaryDirectory(prefix='.setup-', dir=runtime_root) as temporary:
            stage = Path(temporary)
            uv = extract_archive(artifacts[0], stage / 'uv')
            llama = extract_archive(artifacts[1], stage / 'llama')
            # Native executable checks happen against staging before activation is removed.
            staged = replace(manifest, source_path=stage / 'runtime-manifest.json', portable_data_root=None,
                             llama_cpp=replace(manifest.llama_cpp, install_dir='llama/' + llama.name))
            validate_macos_arm64(staged)
            inventory = probe_llama_devices(staged.llama_server_path, timeout_seconds=manifest.llama_cpp.server.device_probe_timeout_seconds)
            device = crosscheck_llama_mtl0(inventory)
            subprocess.run([str(uv / 'uv'), '--version'], check=True, capture_output=True)
            models = stage / 'models'
            models.mkdir()
            for artifact, spec in zip(artifacts[2:], specs[2:]):
                shutil.copyfile(artifact, models / spec.filename)
                verify_artifact(models / spec.filename, spec)
            destinations = ((uv, manifest.uv_install_dir), (llama, manifest.llama_install_dir), (models, manifest.model_install_dir))
            # Recover a killed promotion, or finish cleanup after a committed one.
            for _, destination in destinations:
                backup = destination.with_name('.' + destination.name + '.setup-backup')
                if backup.exists():
                    if manifest.activation_record_path.exists() and destination.exists():
                        shutil.rmtree(backup)
                    else:
                        if destination.exists():
                            shutil.rmtree(destination)
                        backup.rename(destination)
            previous_activation = manifest.activation_record_path.read_bytes() if manifest.activation_record_path.exists() else None
            backups: list[tuple[Path, Path, bool]] = []
            try:
                # An interrupted promotion leaves an explicit unactivated state.
                manifest.activation_record_path.unlink(missing_ok=True)
                for source, destination in destinations:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    backup = destination.with_name('.' + destination.name + '.setup-backup')
                    existed = destination.exists()
                    if existed:
                        destination.rename(backup)
                    backups.append((destination, backup, existed))
                    source.rename(destination)
                activation = write_runtime_activation(manifest)
            except BaseException:
                for destination, backup, existed in reversed(backups):
                    if destination.exists():
                        shutil.rmtree(destination)
                    if existed:
                        backup.rename(destination)
                if previous_activation is not None:
                    manifest.activation_record_path.write_bytes(previous_activation)
                raise
            for _, backup, existed in backups:
                if existed:
                    shutil.rmtree(backup)
    return {'semantic_installed': True, 'ocr_installed': False, 'device': device,
            'native_architecture': 'arm64', 'runtime_fingerprint': manifest.runtime_fingerprint,
            'recipe_fingerprint': manifest.recipe_fingerprint, 'activation': str(activation),
            'setup_seconds': round(time.monotonic() - started, 3),
            'message': 'Semantic runtime installed. CPU OCR provisioning requires ticket05; run an application-session health check before indexing.'}


def run_setup(*, downloads: Path | None = None, offline: bool = False) -> dict:
    if resolve_runtime_target() != 'macos-arm64':
        raise ValueError('Run setup natively on an Apple Silicon Mac.')
    manifest = load_runtime_manifest(portable_data_root=AppPaths.discover().data_root)
    # Ticket05 extension point: provision OCR at its final path using this verified uv.
    return install_semantic_runtime(manifest, downloads=downloads, offline=offline)


def main() -> None:
    parser = argparse.ArgumentParser(description='Install the pinned Mac semantic runtime (OCR extension: ticket05)')
    parser.add_argument('--downloads', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args()
    if args.data_root:
        os.environ['MEMESORT_DATA_ROOT'] = str(args.data_root.expanduser().resolve())
    print(json.dumps(run_setup(downloads=args.downloads, offline=args.offline), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
