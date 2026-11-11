"""Verified Mac semantic and isolated CPU OCR setup for frozen apps and checkouts."""
from __future__ import annotations

import argparse
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
            'message': 'Semantic runtime installed; run an application-session health check before indexing.'}


def install_cpu_ocr(manifest: RuntimeManifest, paths: AppPaths, *, offline: bool = False) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    started = time.monotonic()
    resources = paths.manifest_path.parent
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    worker = resources / 'scripts/paddle_ocr_worker.py'
    requirements = resources / 'requirements-ocr.txt'
    for resource in (worker, requirements):
        if not resource.is_file():
            raise ValueError(f'Missing OCR setup resource: {resource}. Replace the application and rerun setup.')
    if offline:
        for name in ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec'):
            model = paths.models_root / 'paddleocr/official_models' / name
            if not all((model / filename).is_file() for filename in ('inference.json', 'inference.pdiparams', 'inference.yml')):
                raise ValueError(f'Offline OCR models are incomplete: {model}. Rerun setup with network access.')
    # uv's base interpreter must survive removal of every developer cache.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('UV_', 'PIP_', 'PYTHON')) and key not in ('VIRTUAL_ENV', 'CONDA_PREFIX', 'DYLD_LIBRARY_PATH', 'LD_LIBRARY_PATH')}
    env.update(UV_PYTHON_INSTALL_DIR=str(paths.runtime_root / 'python'),
               UV_CACHE_DIR=str(paths.runtime_root / 'uv-cache'),
               PADDLE_PDX_CACHE_HOME=str(paths.models_root / 'paddleocr'),
               PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    uv = [str(manifest.uv_install_dir / 'uv'), '--no-config', *(['--offline'] if offline else [])]
    python = paths.runtime_root / 'ocr-venv/bin/python'
    version = manifest.toolchain.python.ocr_version

    def run(argv, **kwargs):
        result = subprocess.run(argv, env=env, capture_output=True, text=True, encoding='utf-8',
                                timeout=900, **kwargs)
        if result.returncode:
            raise RuntimeError(f'CPU OCR setup failed ({argv[0]}): {result.stderr or result.stdout}. Rerun setup with network access.')
        return result.stdout

    run([*uv, 'python', 'install', version, '--no-bin'])
    run([*uv, 'venv', str(python.parent.parent), '--python', version, '--managed-python', '--allow-existing'])
    run([*uv, 'pip', 'install', '--python', str(python), '--only-binary', ':all:',
         '--default-index', 'https://pypi.org/simple', 'paddlepaddle==3.2.2', '-r', str(requirements)])
    identity = json.loads(run([str(python), '-c',
        "import sys,json,platform; from importlib.metadata import version; "
        "print(json.dumps(dict(python=platform.python_version(),base_prefix=sys.base_prefix,"
        "paddlepaddle=version('paddlepaddle'),paddleocr=version('paddleocr'))))"]))
    if (identity['python'], identity['paddlepaddle'], identity['paddleocr']) != (version, '3.2.2', '3.6.0'):
        raise ValueError(f'Incorrect CPU OCR runtime: {identity}')
    if not Path(identity['base_prefix']).resolve().is_relative_to((paths.runtime_root / 'python').resolve()):
        raise ValueError(f'OCR base Python must be installed under the data root: {identity}')
    with tempfile.TemporaryDirectory(prefix='.ocr-smoke-', dir=paths.runtime_root) as temporary:
        image_path = Path(temporary) / 'hello.png'
        image = Image.new('RGB', (560, 100), 'white')
        ImageDraw.Draw(image).text((15, 20), 'HELLO MEMESORT', fill='black', font=ImageFont.load_default(size=48))
        image.save(image_path)
        # Loading this worker loads both PP-OCRv5 mobile models, then performs CPU inference.
        response = json.loads(run([str(python), str(worker), '--lang', 'ch', '--device', 'cpu'],
                                  input=json.dumps({'id': 'setup-smoke', 'path': str(image_path)}) + '\n'))
        if response.get('error') or response.get('id') != 'setup-smoke' or 'HELLO MEMESORT' not in str(response.get('result', {}).get('text', '')):
            raise ValueError(f'CPU OCR smoke request failed: {response}')
    return {'ocr_installed': True, 'ocr_runtime': identity, 'ocr_smoke': response['result'],
            'ocr_setup_seconds': round(time.monotonic() - started, 3)}


def run_setup(*, downloads: Path | None = None, offline: bool = False) -> dict:
    if resolve_runtime_target() != 'macos-arm64':
        raise ValueError('Run setup natively on an Apple Silicon Mac.')
    import fcntl

    started = time.monotonic()
    paths = AppPaths.discover()
    manifest = load_runtime_manifest(portable_data_root=paths.data_root)
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    with (paths.runtime_root / 'setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another runtime setup is running.') from exc
        report = install_semantic_runtime(manifest, downloads=downloads, offline=offline)
        report.update(install_cpu_ocr(manifest, paths, offline=offline))
    report.update(setup_seconds=round(time.monotonic() - started, 3),
                  message='Semantic runtime and CPU OCR installed. Run an application-session health check before indexing.')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='Install the pinned Mac semantic runtime and CPU OCR')
    parser.add_argument('--downloads', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args()
    if args.data_root:
        os.environ['MEMESORT_DATA_ROOT'] = str(args.data_root.expanduser().resolve())
    print(json.dumps(run_setup(downloads=args.downloads, offline=args.offline), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
