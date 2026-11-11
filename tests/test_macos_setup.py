from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from memesort_worker.runtime_manifest import load_runtime_manifest


class MacSetupTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX symlinks and executable installation')
    def test_setup_verifies_before_activation_preserves_links_and_reruns_offline(self):
        from memesort_worker.macos_setup import install_semantic_runtime
        with tempfile.TemporaryDirectory(prefix='Setup 中文 ') as temporary:
            root = Path(temporary)
            downloads = root / 'download cache'
            downloads.mkdir()
            manifest = load_runtime_manifest(target='macos-arm64', portable_data_root=root / 'Application Support')
            def archive(spec, executable):
                path = downloads / spec.filename
                with tarfile.open(path, 'w:gz') as tar:
                    for name, data in ((executable, bytes.fromhex('cffaedfe0c000001')), ('lib.dylib.1', b'library')):
                        info = tarfile.TarInfo('wrapper/' + name)
                        info.size = len(data)
                        info.mode = 0o755
                        tar.addfile(info, io.BytesIO(data))
                    link = tarfile.TarInfo('wrapper/lib.dylib')
                    link.type = tarfile.SYMTYPE
                    link.linkname = 'lib.dylib.1'
                    tar.addfile(link)
                return replace(spec, size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            uv = replace(manifest.toolchain.uv, archive=archive(manifest.toolchain.uv.archive, 'uv'))
            manifest = replace(manifest, toolchain=replace(manifest.toolchain, uv=uv), llama_cpp=replace(manifest.llama_cpp, archive=archive(manifest.llama_cpp.archive, 'llama-server')))
            def model(spec):
                data = spec.filename.encode()
                (downloads / spec.filename).write_bytes(data)
                return replace(spec, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            manifest = replace(manifest, model=replace(manifest.model, main=model(manifest.model.main), projector=model(manifest.model.projector)))
            with patch('memesort_worker.macos_setup.validate_macos_arm64'), patch('memesort_worker.macos_setup.probe_llama_devices', return_value='MTL0: Apple Test (100 MiB)'), patch('memesort_worker.macos_setup.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
                report = install_semantic_runtime(manifest, downloads=downloads, offline=True)
                activation = manifest.activation_record_path.read_bytes()
                self.assertFalse(report['ocr_installed'])
                self.assertTrue((manifest.llama_install_dir / 'lib.dylib').is_symlink())
                self.assertEqual(b'library', (manifest.llama_install_dir / 'lib.dylib').read_bytes())
                self.assertTrue(manifest.llama_server_path.stat().st_mode & 0o100)
                install_semantic_runtime(manifest, downloads=downloads, offline=True)
                self.assertEqual(activation, manifest.activation_record_path.read_bytes())
                # A failed final activation restores every previously installed directory.
                with patch('memesort_worker.macos_setup.write_runtime_activation', side_effect=OSError('interrupted activation')):
                    with self.assertRaisesRegex(OSError, 'interrupted activation'):
                        install_semantic_runtime(manifest, downloads=downloads, offline=True)
                self.assertEqual(activation, manifest.activation_record_path.read_bytes())
                self.assertEqual(b'library', (manifest.llama_install_dir / 'lib.dylib').read_bytes())
                # Simulate a process killed halfway through directory promotion.
                backup = manifest.llama_install_dir.with_name('.' + manifest.llama_install_dir.name + '.setup-backup')
                manifest.llama_install_dir.rename(backup)
                manifest.llama_install_dir.mkdir()
                (manifest.llama_install_dir / 'partial').write_text('interrupted')
                manifest.activation_record_path.unlink()
                install_semantic_runtime(manifest, downloads=downloads, offline=True)
                self.assertFalse(backup.exists())
                self.assertEqual(activation, manifest.activation_record_path.read_bytes())
                self.assertTrue((manifest.llama_install_dir / 'lib.dylib').is_symlink())
                (downloads / manifest.model.main.filename).write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'verification'):
                    install_semantic_runtime(manifest, downloads=downloads, offline=True)
                self.assertEqual(activation, manifest.activation_record_path.read_bytes())

    def test_archive_escape_is_rejected_before_any_installation(self):
        from memesort_worker.macos_setup import extract_archive
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, link in (('../escape', None), ('/absolute', None), ('wrapper/link', '../../escape'), ('wrapper/link', '../escape')):
                path = root / 'bad.tar.gz'
                with tarfile.open(path, 'w:gz') as tar:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = link
                    tar.addfile(member)
                with self.subTest(name=name), self.assertRaises((ValueError, tarfile.FilterError)):
                    extract_archive(path, root / 'stage')
            self.assertFalse((root / 'escape').exists())


class CpuOcrSetupTests(unittest.TestCase):
    def test_setup_uses_final_data_paths_and_exercises_the_cpu_worker(self):
        from memesort_worker.app_paths import AppPaths
        from memesort_worker.macos_setup import install_cpu_ocr

        with tempfile.TemporaryDirectory(prefix='OCR 中文 ') as temporary:
            root = Path(temporary).resolve()
            paths = AppPaths.discover({'MEMESORT_DATA_ROOT': str(root)})
            manifest = load_runtime_manifest(target='macos-arm64', portable_data_root=root)
            for name in ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec'):
                model = paths.models_root / 'paddleocr/official_models' / name
                model.mkdir(parents=True)
                for filename in ('inference.json', 'inference.pdiparams', 'inference.yml'):
                    (model / filename).write_text('fixture')
            identity = {'python': '3.12.13', 'paddlepaddle': '3.2.2', 'paddleocr': '3.6.0',
                        'base_prefix': str(paths.runtime_root / 'python' / 'cpython-test')}
            calls = []

            def command(argv, **kwargs):
                calls.append((argv, kwargs))
                stdout = ''
                if '-c' in argv:
                    stdout = json.dumps(identity)
                if str(paths.manifest_path.parent / 'scripts/paddle_ocr_worker.py') in argv:
                    self.assertEqual('cpu', argv[-1])
                    request = json.loads(kwargs['input'])
                    self.assertTrue(Path(request['path']).is_file())
                    stdout = json.dumps({'id': request['id'], 'result': {'text': 'HELLO MEMESORT'}}) + '\n'
                return subprocess.CompletedProcess(argv, 0, stdout, '')

            with patch.dict(os.environ, {'UV_PYTHON_INSTALL_DIR': '/developer/python', 'UV_CACHE_DIR': '/developer/cache',
                                         'PADDLE_PDX_CACHE_HOME': '/developer/models', 'PYTHONHOME': '/developer/python',
                                         'PYTHONPATH': '/developer/modules'}), patch('memesort_worker.macos_setup.subprocess.run', side_effect=command):
                report = install_cpu_ocr(manifest, paths, offline=True)
            self.assertTrue(report['ocr_installed'])
            self.assertEqual(identity, report['ocr_runtime'])
            uv_commands = [argv for argv, _ in calls if argv[0] == str(manifest.uv_install_dir / 'uv')]
            self.assertTrue(all('--offline' in argv for argv in uv_commands))
            self.assertTrue(any('venv' in argv and str(paths.runtime_root / 'ocr-venv') in argv for argv in uv_commands))
            self.assertTrue(any('paddlepaddle==3.2.2' in argv and str(paths.manifest_path.parent / 'requirements-ocr.txt') in argv for argv in uv_commands))
            for _, kwargs in calls:
                self.assertEqual(str(paths.runtime_root / 'python'), kwargs['env']['UV_PYTHON_INSTALL_DIR'])
                self.assertEqual(str(paths.runtime_root / 'uv-cache'), kwargs['env']['UV_CACHE_DIR'])
                self.assertEqual(str(paths.models_root / 'paddleocr'), kwargs['env']['PADDLE_PDX_CACHE_HOME'])
                self.assertNotIn('PYTHONHOME', kwargs['env'])
                self.assertNotIn('PYTHONPATH', kwargs['env'])

    def test_offline_setup_rejects_missing_models_before_worker_can_download(self):
        from memesort_worker.app_paths import AppPaths
        from memesort_worker.macos_setup import install_cpu_ocr

        with tempfile.TemporaryDirectory() as temporary:
            paths = AppPaths.discover({'MEMESORT_DATA_ROOT': temporary})
            manifest = load_runtime_manifest(target='macos-arm64', portable_data_root=temporary)
            with patch('memesort_worker.macos_setup.subprocess.run') as command:
                with self.assertRaisesRegex(ValueError, 'Offline OCR models are incomplete'):
                    install_cpu_ocr(manifest, paths, offline=True)
            command.assert_not_called()
