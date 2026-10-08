from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
import unittest
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from memesort_worker.embedding_backend import (
    EmbeddingBackendError,
    LlamaCppEmbeddingBackend,
)
from memesort_worker.inference_service import InferenceScheduler
from memesort_worker.recipe_provider import default_provider
from memesort_worker.library import (
    RuntimeHealthResult,
)
from memesort_worker.llama_cpp_backend import (
    LlamaCppBackendError,
    LlamaCppEmbeddingAdapter,
    LlamaCppServer,
    _close_runtime_loggers,
    load_server_config,
    verify_model_bundle,
)
from memesort_worker.runtime_activation import RuntimeActivationError, validate_runtime_activation, write_runtime_activation
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.runtime_admission import VulkanDeviceInfo
from memesort_worker.pinned_runtime import PinnedRuntime
from memesort_worker.runtime_service import run_runtime_health_check
from memesort_worker.runtime_service import (
    _save_last_health_check,
    get_last_health_check,
)


class LlamaCppBackendTests(unittest.TestCase):
    def _write_bundle(self, root: Path) -> tuple[Path, Path]:
        main_model = root / "embeddinggemma-2-Q8_0.gguf"
        mmproj = root / "mmproj-Q8_0.gguf"
        main_model.write_bytes(b"gguf-main")
        mmproj.write_bytes(b"gguf-mmproj")
        return main_model, mmproj

    def test_verified_recipe_rejects_different_gguf_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_model, mmproj = self._write_bundle(Path(temp_dir))

            with self.assertRaisesRegex(LlamaCppBackendError, "Unexpected SHA256"):
                verify_model_bundle(main_model, mmproj)

    def test_adapter_prefixes_every_text_query_at_the_http_boundary(self) -> None:
        adapter = LlamaCppEmbeddingAdapter(load_server_config())
        self.addCleanup(adapter.close)
        adapter.server._base_url = "http://127.0.0.1:8080"
        for instruction in (None, "task: search result | query: "):
            with self.subTest(instruction=instruction), patch.object(
                adapter.server, "_ensure_ready"
            ), patch("memesort_worker.llama_cpp_backend.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = (
                    b'{"data": [{"embedding": [1.0, 0.0]}]}'
                )
                adapter.embed_text("开心", instruction=instruction)
                payload = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual("task: search result | query: 开心", payload["input"])

    def test_adapter_rejects_text_instructions_outside_the_pinned_recipe(self) -> None:
        adapter = LlamaCppEmbeddingAdapter(load_server_config())
        self.addCleanup(adapter.close)
        adapter.server._base_url = "http://127.0.0.1:8080"
        with patch.object(adapter.server, "_ensure_ready"), patch(
            "memesort_worker.llama_cpp_backend.urlopen"
        ) as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"data": [{"embedding": [1.0, 0.0]}]}'
            )
            with self.assertRaisesRegex(LlamaCppBackendError, "instruction diverged"):
                adapter.embed_text("开心", instruction="Retrieve images")
            urlopen.assert_not_called()

    def test_adapter_sends_image_only_media_at_the_http_boundary(self) -> None:
        config = load_server_config()
        adapter = LlamaCppEmbeddingAdapter(config)
        self.addCleanup(adapter.close)
        adapter.server._base_url = "http://127.0.0.1:8080"
        with patch.object(adapter.server, "_ensure_ready"), patch(
            "memesort_worker.llama_cpp_backend.urlopen"
        ) as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"data": [{"embedding": [1.0, 0.0]}]}'
            )
            adapter.embed_image_bytes(b"image", instruction="Retrieve images")
            payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(
            [{"prompt_string": "<__media__>", "multimodal_data": ["aW1hZ2U="]}],
            payload["input"],
        )

    def test_server_parses_openai_embedding_response(self) -> None:
        server = LlamaCppServer(load_server_config())
        server._base_url = "http://127.0.0.1:8080"
        with patch.object(server, "_ensure_ready"), patch.object(
            server, "_request_json", return_value={"data": [{"embedding": [0.25, 0.75]}]}
        ) as request:
            vector = server.request_embedding("hello")

        np.testing.assert_allclose(np.array([0.25, 0.75], dtype=np.float32), vector)
        self.assertEqual(
            load_runtime_manifest().model.request_model,
            request.call_args.kwargs["payload"]["model"],
        )
        server.close()

    def test_server_config_is_fully_derived_from_manifest(self) -> None:
        manifest = load_runtime_manifest()
        config = load_server_config()

        self.assertEqual(manifest.llama_server_path, config.executable_path)
        self.assertEqual(manifest.main_model_path, config.model_path)
        self.assertEqual(manifest.projector_path, config.mmproj_path)
        self.assertEqual("Vulkan0", config.device)
        self.assertEqual(manifest.llama_cpp.server.parallel_slots, config.parallel_slots)
        self.assertEqual(manifest.model.request_model, config.request_model)
        self.assertEqual(manifest.llama_cpp.server.idle_timeout_seconds, config.idle_timeout_seconds)
        self.assertEqual(manifest.logging.file_count, config.log_file_count)
        self.assertEqual(manifest.logging.max_bytes_per_file, config.log_max_bytes)

    def test_embedding_backend_rejects_dimension_mismatch(self) -> None:
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=np.array([3.0, 4.0, 12.0], dtype=np.float32),
        ):
            backend = LlamaCppEmbeddingBackend(InferenceScheduler())
            with self.assertRaisesRegex(EmbeddingBackendError, "expected exactly 2"):
                backend.embed_text("hello", output_dimension=2)

    def test_embedding_backend_returns_normalized_fp32(self) -> None:
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=np.array([3.0, 4.0], dtype=np.float64),
        ):
            vector = LlamaCppEmbeddingBackend(InferenceScheduler()).embed_text(
                "hello", output_dimension=2
            )

        self.assertEqual(np.dtype(np.float32), vector.dtype)
        np.testing.assert_allclose(np.array([0.6, 0.8], dtype=np.float32), vector)
        self.assertAlmostEqual(1.0, float(np.linalg.norm(vector)), places=6)

    def test_embedding_backend_normalizes_finite_gemma_vectors_at_float32_limits(self) -> None:
        large_vector = np.full(768, np.finfo(np.float32).max, dtype=np.float32)
        with patch(
            "memesort_worker.llama_cpp_backend.LlamaCppEmbeddingAdapter.embed_text",
            return_value=large_vector,
        ):
            backend = LlamaCppEmbeddingBackend(InferenceScheduler())
            self.addCleanup(backend.close)
            vector = backend.embed_text("large finite vector", output_dimension=768)

        self.assertEqual(np.dtype(np.float32), vector.dtype)
        self.assertTrue(np.isfinite(vector).all())
        self.assertAlmostEqual(1.0, float(np.linalg.norm(vector.astype(np.float64))), places=6)

    def test_embedding_backend_rejects_zero_and_non_finite_vectors(self) -> None:
        backend = LlamaCppEmbeddingBackend(InferenceScheduler())
        for vector, message in (
            (np.zeros(2, dtype=np.float32), "zero vector"),
            (np.array([np.nan, 1.0], dtype=np.float32), "NaN or infinite"),
            (np.array([np.inf, 1.0], dtype=np.float32), "NaN or infinite"),
        ):
            with self.subTest(vector=vector), patch.object(
                backend._adapter, "embed_text", return_value=vector
            ):
                with self.assertRaisesRegex(EmbeddingBackendError, message):
                    backend.embed_text("hello", output_dimension=2)

    def test_managed_server_command_uses_manifest_vulkan_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "llama-server.exe"
            model = root / "model.gguf"
            projector = root / "mmproj.gguf"
            for path in (executable, model, projector):
                path.write_bytes(b"test")
            config = replace(
                load_server_config(),
                executable_path=executable,
                model_path=model,
                mmproj_path=projector,
            )
            server = LlamaCppServer(config)
            with patch.dict("os.environ", {
                "GGML_VK_DISABLE_F16": "0",
                "LLAMA_ARG_IMAGE_MIN_TOKENS": "280",
                "LLAMA_ARG_IMAGE_MAX_TOKENS": "280",
            }), patch(
                "memesort_worker.llama_cpp_backend.subprocess.Popen"
            ) as popen, patch(
                "memesort_worker.llama_cpp_backend._validate_manifest_runtime"
            ), patch.object(server, "_wait_until_healthy"):
                popen.return_value.poll.return_value = None
                _ = server.base_url

            command = popen.call_args.args[0]
            self.assertEqual("Vulkan0", command[command.index("--device") + 1])
            self.assertEqual(
                str(config.parallel_slots), command[command.index("--parallel") + 1]
            )
            self.assertEqual(config.pooling, command[command.index("--pooling") + 1])
            self.assertEqual("2", command[command.index("--embd-normalize") + 1])
            self.assertEqual("mean", command[command.index("--pooling") + 1])
            self.assertEqual("2048", command[command.index("--ctx-size") + 1])
            self.assertEqual("2048", command[command.index("--batch-size") + 1])
            self.assertEqual("2048", command[command.index("--ubatch-size") + 1])
            self.assertEqual("off", command[command.index("--flash-attn") + 1])
            self.assertEqual("99", command[command.index("--n-gpu-layers") + 1])
            self.assertEqual("1", command[command.index("--parallel") + 1])
            self.assertEqual("1", popen.call_args.kwargs["env"]["GGML_VK_DISABLE_F16"])
            self.assertEqual("<__media__>", popen.call_args.kwargs["env"]["LLAMA_MEDIA_MARKER"])
            self.assertNotIn("--image-max-tokens", command)
            self.assertNotIn("--image-min-tokens", command)
            self.assertFalse("LLAMA_ARG_IMAGE_MIN_TOKENS" in popen.call_args.kwargs["env"])
            self.assertFalse("LLAMA_ARG_IMAGE_MAX_TOKENS" in popen.call_args.kwargs["env"])
            self.assertIn("--log-disable", command)
            self.assertEqual(
                subprocess.DEVNULL,
                popen.call_args.kwargs["stdout"],
            )
            server.close()

    def test_request_failure_restarts_and_retries_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(load_server_config(), log_dir=Path(temp_dir))
            server = LlamaCppServer(config)
            server._base_url = "http://127.0.0.1:8080"
            with patch.object(server, "_ensure_ready"), patch.object(
                server,
                "_request_json",
                side_effect=[
                    LlamaCppBackendError("connection failed"),
                    {"data": [{"embedding": [1.0, 2.0]}]},
                ],
            ) as request:
                vector = server.request_embedding("private prompt")

            np.testing.assert_allclose(np.array([1.0, 2.0], dtype=np.float32), vector)
            self.assertEqual(2, request.call_count)
            failing_server = LlamaCppServer(config)
            with patch.object(failing_server, "_ensure_ready"), patch.object(
                failing_server,
                "_request_json",
                side_effect=LlamaCppBackendError("still failed"),
            ) as failed_request:
                with self.assertRaisesRegex(LlamaCppBackendError, "still failed"):
                    failing_server.request_embedding("another private prompt")
            self.assertEqual(2, failed_request.call_count)
            for handler in server._logger.handlers:
                handler.flush()
            log_text = (Path(temp_dir) / "inference.log").read_text(encoding="utf-8")
            self.assertIn("restarting_once", log_text)
            self.assertNotIn("private prompt", log_text)
            server.close()
            failing_server.close()
            _close_runtime_loggers()

    def test_server_unloads_after_manifest_idle_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = [root / "llama-server.exe", root / "model.gguf", root / "mmproj.gguf"]
            for path in paths:
                path.write_bytes(b"test")
            config = replace(
                load_server_config(),
                executable_path=paths[0],
                model_path=paths[1],
                mmproj_path=paths[2],
                idle_timeout_seconds=0.05,
                log_dir=root / "logs",
            )
            server = LlamaCppServer(config)
            with patch(
                "memesort_worker.llama_cpp_backend._validate_manifest_runtime"
            ), patch(
                "memesort_worker.llama_cpp_backend.subprocess.Popen"
            ) as popen, patch.object(server, "_wait_until_healthy"):
                popen.return_value.poll.return_value = None
                _ = server.base_url
                deadline = time.monotonic() + 1
                while not popen.return_value.terminate.called and time.monotonic() < deadline:
                    time.sleep(0.01)

            self.assertTrue(popen.return_value.terminate.called)
            self.assertIsNone(server._process)
            _close_runtime_loggers()

    def test_runtime_descriptor_identifies_the_pinned_vulkan_backend(self) -> None:
        runtime = get_runtime_descriptor()

        self.assertEqual("llama.cpp", runtime.backend_name)
        self.assertEqual("Vulkan0", runtime.device)
        self.assertEqual(load_runtime_manifest().llama_cpp.build, runtime.llama_cpp_build)

    def test_vulkan_recipe_identity_is_derived_from_manifest(self) -> None:
        manifest = load_runtime_manifest()
        recipe = default_provider().manifest_recipe

        self.assertEqual(manifest.model.id, recipe["model_id"])
        self.assertEqual(manifest.recipe_fingerprint, recipe["model_revision"])
        self.assertEqual(manifest.model.output_dimension, recipe["output_dimension"])
        self.assertEqual(manifest.preprocessing.version, recipe["preprocess_version"])
        self.assertEqual(manifest.embedding.instruction_id, recipe["instruction_key"])

    def test_persisted_health_cannot_authorize_a_new_app_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            passed = RuntimeHealthResult(
                runtime_fingerprint=load_runtime_manifest().runtime_fingerprint,
                backend_name="llama.cpp",
                device="Vulkan0",
                gpu_name="Vulkan0: Test GPU",
                gpu_vendor="amd",
                gpu_vendor_id="0x1002",
                text_smoke_vector_dim=768,
                image_smoke_vector_dim=768,
                diagnostic_steps=[],
                smoke_test_ok=True,
                error=None,
            )
            _save_last_health_check(library_root, passed)
            runtime = PinnedRuntime(library_root)
            try:
                with patch.object(Path, "is_file", return_value=True):
                    ready, detail = runtime.is_ready_for_indexing()
            finally:
                runtime.close()
            persisted = get_last_health_check(library_root)

        self.assertFalse(ready)
        self.assertIn("session", detail.lower())
        self.assertIsNotNone(persisted)

    def test_gemma_runtime_authorization_reports_identity_and_validates_http_inputs(self) -> None:
        default_provider.cache_clear()
        self.addCleanup(default_provider.cache_clear)
        raw = json.loads(load_runtime_manifest().source_path.read_text())
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "runtime-manifest.json"
            for key, payload in (("main", b"gguf-main"), ("projector", b"gguf-mmproj")):
                raw["model"][key]["size_bytes"] = len(payload)
                raw["model"][key]["sha256"] = hashlib.sha256(payload).hexdigest()
            manifest_path.write_text(json.dumps(raw))
            manifest = load_runtime_manifest(manifest_path)
            for path, payload in (
                (manifest.llama_server_path, b"server"),
                (manifest.main_model_path, b"gguf-main"),
                (manifest.projector_path, b"gguf-mmproj"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            write_runtime_activation(manifest)
            inputs = []

            def fake_http(request, timeout=None):
                if request.get_method() == "GET":
                    return io.BytesIO(b'{"status": "ok"}')
                payload = json.loads(request.data)
                inputs.append(payload["input"])
                return io.BytesIO(json.dumps({"data": [{"embedding": [1.0] + [0.0] * 767}]}).encode())

            with patch.dict("os.environ", {"MEMESORT_APP_ROOT": str(root)}), patch(
                "memesort_worker.runtime_service.probe_vulkan0",
                return_value=VulkanDeviceInfo(0, 0x1002, "amd", 1, "Test GPU"),
            ), patch(
                "memesort_worker.llama_cpp_backend.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, b"Vulkan0: Test GPU", b""),
            ), patch("memesort_worker.llama_cpp_backend.subprocess.Popen") as popen, patch(
                "memesort_worker.llama_cpp_backend.urlopen", side_effect=fake_http
            ):
                popen.return_value.poll.return_value = None
                runtime = PinnedRuntime(root / "library")
                try:
                    result = runtime.authorize()
                    self.assertTrue(runtime.is_ready_for_indexing()[0])
                    self.assertEqual(768, result.text_smoke_vector_dim)
                    self.assertEqual(768, result.image_smoke_vector_dim)
                    detail = result.diagnostic_steps[0]["detail"]
                    self.assertIn("b11457", detail)
                    self.assertIn("unsloth/embeddinggemma-2-GGUF:Q8_0", detail)
                    self.assertEqual("task: search result | query: confused reaction image", inputs[0])
                    self.assertEqual("<__media__>", inputs[1][0]["prompt_string"])
                    self.assertTrue(inputs[1][0]["multimodal_data"][0])
                    raw["llama_cpp"]["server"]["batch_size"] = 1024
                    manifest_path.write_text(json.dumps(raw))
                    ready, reason = runtime.is_ready_for_indexing()
                    self.assertFalse(ready)
                    self.assertIn("stale", reason)
                    with self.assertRaisesRegex(RuntimeActivationError, "does not match"):
                        validate_runtime_activation(load_runtime_manifest())
                finally:
                    runtime.close()
                    _close_runtime_loggers()

    def test_vulkan_health_check_does_not_download_missing_gguf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_manifest = replace(
                load_runtime_manifest(),
                source_path=Path(temp_dir) / "runtime-manifest.json",
            )
            with patch(
                "memesort_worker.runtime_service.load_runtime_manifest",
                return_value=missing_manifest,
            ):
                result = run_runtime_health_check()

        self.assertFalse(result.smoke_test_ok)
        self.assertEqual("llama.cpp", result.backend_name)
        self.assertIn("GGUF", result.error)

    def test_vulkan_health_check_validates_text_and_image_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portable_root = Path(temp_dir) / "MemeSortData"
            manifest = load_runtime_manifest(portable_data_root=portable_root)
            server = manifest.llama_server_path
            main_model = manifest.main_model_path
            projector = manifest.projector_path
            main_payload = b"gguf-main"
            projector_payload = b"gguf-mmproj"
            for path, payload in (
                (server, b"llama-server"),
                (main_model, main_payload),
                (projector, projector_payload),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            manifest = replace(
                manifest,
                model=replace(
                    manifest.model,
                    main=replace(
                        manifest.model.main,
                        size_bytes=len(main_payload),
                    ),
                    projector=replace(
                        manifest.model.projector,
                        size_bytes=len(projector_payload),
                    ),
                ),
            )
            with patch(
                "memesort_worker.runtime_service.load_runtime_manifest",
                return_value=manifest,
            ):
                with patch(
                    "memesort_worker.llama_cpp_backend.discover_llama_server",
                    return_value=server,
                ):
                    with patch(
                        "memesort_worker.llama_cpp_backend.probe_llama_devices",
                        return_value="Vulkan0: Test GPU",
                    ):
                        with patch(
                            "memesort_worker.runtime_service.probe_vulkan0",
                            return_value=VulkanDeviceInfo(
                                index=0,
                                vendor_id=0x1002,
                                vendor_name="amd",
                                device_id=1,
                                device_name="Test GPU",
                            ),
                        ):
                            with patch(
                                "memesort_worker.runtime_service.validate_runtime_activation"
                            ):
                                with patch(
                                    "memesort_worker.llama_cpp_backend.verify_model_bundle"
                                ) as verify_bundle:
                                    backend = Mock()
                                    backend.embed_text.return_value = np.ones(
                                        768, dtype=np.float32
                                    )
                                    backend.embed_image_bytes.return_value = np.ones(
                                        768, dtype=np.float32
                                    )
                                    result = run_runtime_health_check(
                                        embedding_backend_factory=lambda: backend
                                    )

            verify_bundle.assert_called_once_with(
                main_model,
                projector,
                manifest,
            )

        self.assertTrue(result.smoke_test_ok)
        self.assertEqual("Vulkan0: Test GPU", result.gpu_name)
        self.assertEqual("amd", result.gpu_vendor)
        self.assertEqual("0x1002", result.gpu_vendor_id)
        self.assertEqual(768, result.image_smoke_vector_dim)
        self.assertEqual("image-embedding-smoke", result.diagnostic_steps[-1]["step"])
        backend.embed_text.assert_called_once()
        backend.embed_image_bytes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
