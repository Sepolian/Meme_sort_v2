from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from memesort_worker.runtime_manifest import load_runtime_manifest


class MacSetupTests(unittest.TestCase):
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
