from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from memesort_worker.runtime_activation import expected_activation_record
from memesort_worker.runtime_manifest import RuntimeManifestError, load_runtime_manifest
from memesort_worker.recipe_provider import from_manifest


class MacRuntimeTests(unittest.TestCase):
    def test_native_target_resolution_and_windows_identity(self):
        from memesort_worker.runtime_manifest import resolve_runtime_target

        for system, machine, expected in (
            ('win32', 'AMD64', 'windows-x86_64'),
            ('win32', 'x86_64', 'windows-x86_64'),
            ('darwin', 'arm64', 'macos-arm64'),
        ):
            self.assertEqual(expected, resolve_runtime_target(system, machine))
        for system, machine in (('darwin', 'x86_64'), ('win32', 'arm64'), ('linux', 'x86_64')):
            with self.assertRaises(RuntimeManifestError):
                resolve_runtime_target(system, machine)
        windows = load_runtime_manifest(target='windows-x86_64')
        baseline = json.loads((Path(__file__).parent / 'fixtures/windows_runtime_identity.json').read_text())
        self.assertEqual(baseline, {
            'recipe_id': windows.recipe_id,
            'recipe_fingerprint': windows.recipe_fingerprint,
            'runtime_fingerprint': windows.runtime_fingerprint,
            'activation_record': expected_activation_record(windows),
        })
        self.assertEqual('11cdd5f9aa94105d47ae568ae95d544af718ca146ea35fedc4328ab693112d3f', windows.recipe_fingerprint)
        self.assertEqual('b3143abdeae31b749a2ff1cf59898f725aca722ecbb28115a1fe7301451c76f1', windows.runtime_fingerprint)
        self.assertEqual('vulkan', from_manifest(windows).manifest_recipe['runtime_profile'])
        self.assertEqual('llama-server.exe', expected_activation_record(windows)['llama_cpp']['executable'])

    def test_mac_descriptor_and_recipe_follow_explicit_mtl0_target(self):
        from memesort_worker.runtime_descriptor import get_runtime_descriptor
        mac = load_runtime_manifest(target='macos-arm64')
        self.assertEqual(('macos', 'arm64', 'MTL0'), (mac.platform.os, mac.platform.architecture, mac.platform.device))
        self.assertEqual('metal', from_manifest(mac).manifest_recipe['runtime_profile'])
        self.assertTrue(mac.recipe_id.startswith('metal-'))
        self.assertFalse(mac.llama_cpp.server.vulkan_disable_f16)
        self.assertEqual({}, mac.platform.vendor_ids)
        with patch('memesort_worker.runtime_descriptor.load_runtime_manifest', return_value=mac):
            descriptor = get_runtime_descriptor()
        self.assertEqual('MTL0', descriptor.device)
        self.assertEqual(768, descriptor.output_dimension)
        self.assertEqual('task: search result | query: ', mac.embedding.instruction)
        self.assertNotEqual(load_runtime_manifest(target='windows-x86_64').recipe_fingerprint, mac.recipe_fingerprint)

    def test_metal_admission_requires_native_host_binary_and_exact_apple_device(self):
        import tempfile
        from memesort_worker.runtime_admission import RuntimeAdmissionError, validate_macos_arm64, crosscheck_llama_mtl0
        with tempfile.TemporaryDirectory() as temp:
            mac = load_runtime_manifest(target='macos-arm64', portable_data_root=temp)
            mac.llama_server_path.parent.mkdir(parents=True)
            mac.llama_server_path.write_bytes(bytes.fromhex('cffaedfe0c000001') + b'fake')
            with patch('memesort_worker.runtime_admission.sys.platform', 'darwin'), patch('memesort_worker.runtime_admission.platform.machine', return_value='arm64'):
                validate_macos_arm64(mac)
                self.assertIn('Apple M6', crosscheck_llama_mtl0('Available devices:\n  MTL0: Apple M6 (12124 MiB, 12123 MiB free)\n  BLAS: Accelerate'))
                for inventory in ('', 'BLAS: Accelerate', 'MTL1: Apple M6', 'MTL0: unknown GPU'):
                    with self.assertRaises(RuntimeAdmissionError):
                        crosscheck_llama_mtl0(inventory)
                mac.llama_server_path.write_bytes(b'wrong binary')
                with self.assertRaises(RuntimeAdmissionError):
                    validate_macos_arm64(mac)
            with patch('memesort_worker.runtime_admission.platform.machine', return_value='x86_64'):
                with self.assertRaises(RuntimeAdmissionError):
                    validate_macos_arm64(mac)

    def test_mac_session_requires_successful_current_text_and_image_health(self):
        import hashlib
        import io
        import os
        import subprocess
        import tempfile
        from memesort_worker.llama_cpp_backend import _close_runtime_loggers
        from memesort_worker.pinned_runtime import PinnedRuntime
        from memesort_worker.runtime_activation import write_runtime_activation
        from memesort_worker.runtime_service import RuntimeAuthorizationError
        from memesort_worker.recipe_provider import default_provider

        default_provider.cache_clear()
        self.addCleanup(default_provider.cache_clear)
        raw = json.loads(load_runtime_manifest(target='macos-arm64').source_path.read_text())
        with tempfile.TemporaryDirectory(prefix='Session 中文 ') as temporary:
            root = Path(temporary)
            for key, data in (('main', b'main'), ('projector', b'projector')):
                raw['model'][key].update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            path = root / 'runtime-manifest.json'
            path.write_text(json.dumps(raw))
            inputs = []
            invalid = False
            def http(request, timeout=None):
                if request.get_method() == 'GET':
                    return io.BytesIO(b'{"status":"ok"}')
                inputs.append(json.loads(request.data)['input'])
                vector = [0.0] * 768 if invalid else [1.0] + [0.0] * 767
                return io.BytesIO(json.dumps({'data': [{'embedding': vector}]}).encode())
            with patch.dict(os.environ, {'MEMESORT_APP_ROOT': str(root), 'MEMESORT_DATA_ROOT': str(root / 'data'), 'GGML_VK_DISABLE_F16': '1'}), patch('memesort_worker.runtime_manifest.resolve_runtime_target', return_value='macos-arm64'), patch('memesort_worker.runtime_admission.sys.platform', 'darwin'), patch('memesort_worker.runtime_admission.platform.machine', return_value='arm64'), patch('memesort_worker.llama_cpp_backend.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'MTL0: Apple Test (100 MiB)', b'')), patch('memesort_worker.llama_cpp_backend.subprocess.Popen') as popen, patch('memesort_worker.llama_cpp_backend.urlopen', side_effect=http):
                manifest = load_runtime_manifest()
                for file, data in ((manifest.llama_server_path, bytes.fromhex('cffaedfe0c000001')), (manifest.main_model_path, b'main'), (manifest.projector_path, b'projector')):
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(data)
                write_runtime_activation(manifest)
                popen.return_value.poll.return_value = None
                runtime = PinnedRuntime(root / 'library')
                try:
                    self.assertFalse(runtime.is_ready_for_indexing()[0])
                    health = runtime.authorize()
                    self.assertTrue(runtime.is_ready_for_indexing()[0])
                    self.assertEqual(('MTL0', 'apple', None, 768, 768), (health.device, health.gpu_vendor, health.gpu_vendor_id, health.text_smoke_vector_dim, health.image_smoke_vector_dim))
                    command = popen.call_args.args[0]
                    self.assertEqual('MTL0', command[command.index('--device') + 1])
                    self.assertNotIn('GGML_VK_DISABLE_F16', popen.call_args.kwargs['env'])
                    self.assertEqual('task: search result | query: confused reaction image', inputs[0])
                    self.assertEqual('<__media__>', inputs[1][0]['prompt_string'])
                    next_session = PinnedRuntime(root / 'library')
                    self.assertFalse(next_session.is_ready_for_indexing()[0])
                    next_session.close()
                    raw['targets']['macos-arm64']['llama_cpp']['server']['batch_size'] = 1024
                    path.write_text(json.dumps(raw))
                    self.assertIn('stale', runtime.is_ready_for_indexing()[1])
                    with self.assertRaises(RuntimeAuthorizationError):
                        runtime.authorize()
                    raw['targets']['macos-arm64']['llama_cpp']['server']['batch_size'] = 2048
                    path.write_text(json.dumps(raw))
                    invalid = True
                    with self.assertRaises(RuntimeAuthorizationError):
                        runtime.authorize()
                    self.assertFalse(runtime.is_ready_for_indexing()[0])
                finally:
                    runtime.close()
                    _close_runtime_loggers()
