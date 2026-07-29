from __future__ import annotations

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
from memesort_worker import library as library_module
from memesort_worker.library import (
    RuntimeHealthResult,
)
from memesort_worker.llama_cpp_backend import (
    LlamaCppBackendError,
    LlamaCppEmbeddingAdapter,
    LlamaCppServer,
    _close_runtime_loggers,
    load_server_config,
    verify_qwen3_vl_embedding_2b_bundle,
)
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.runtime_admission import VulkanDeviceInfo
from memesort_worker.runtime_service import run_runtime_health_check
from memesort_worker.runtime_service import (
    _clear_current_health_checks,
    _save_last_health_check,
    get_last_health_check,
    is_runtime_ready_for_indexing,
)


class LlamaCppBackendTests(unittest.TestCase):
    def _write_bundle(self, root: Path) -> tuple[Path, Path]:
        main_model = root / "Qwen3-VL-Embedding-2B.Q4_K_M.gguf"
        mmproj = root / "mmproj-Qwen3-VL-Embedding-2B.f16.gguf"
        main_model.write_bytes(b"gguf-main")
        mmproj.write_bytes(b"gguf-mmproj")
        return main_model, mmproj

    def test_verified_recipe_rejects_different_gguf_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            main_model, mmproj = self._write_bundle(Path(temp_dir))

            with self.assertRaisesRegex(LlamaCppBackendError, "Unexpected SHA256"):
                verify_qwen3_vl_embedding_2b_bundle(main_model, mmproj)

    def test_adapter_sends_multimodal_embedding_payload(self) -> None:
        config = load_server_config()
        adapter = LlamaCppEmbeddingAdapter(config)
        expected = np.array([1.0, 0.0], dtype=np.float32)
        with patch.object(adapter.server, "request_embedding", return_value=expected) as request:
            result = adapter.embed_image_bytes(b"image", instruction="Retrieve images")

        self.assertIs(result, expected)
        request_input = request.call_args.args[0]
        self.assertEqual(1, len(request_input))
        self.assertIn(config.media_marker, request_input[0]["prompt_string"])
        self.assertEqual(["aW1hZ2U="], request_input[0]["multimodal_data"])

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
            with patch(
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
        recipe = library_module.MANIFEST_RECIPE

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
                text_smoke_vector_dim=2048,
                image_smoke_vector_dim=2048,
                diagnostic_steps=[],
                smoke_test_ok=True,
                error=None,
            )
            _save_last_health_check(library_root, passed)
            with patch.object(Path, "is_file", return_value=True):
                ready_in_session, _ = is_runtime_ready_for_indexing(library_root)
            _clear_current_health_checks()
            with patch.object(Path, "is_file", return_value=True):
                ready, detail = is_runtime_ready_for_indexing(library_root)
            persisted = get_last_health_check(library_root)

        self.assertTrue(ready_in_session)
        self.assertFalse(ready)
        self.assertIn("session", detail.lower())
        self.assertIsNotNone(persisted)

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
            bundle = Path(temp_dir)
            self._write_bundle(bundle)
            with patch(
                "memesort_worker.llama_cpp_backend.discover_llama_server",
                return_value=Path("llama-server.exe"),
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
                                "memesort_worker.llama_cpp_backend.verify_qwen3_vl_embedding_2b_bundle"
                            ):
                                backend = Mock()
                                backend.embed_text.return_value = np.ones(2048, dtype=np.float32)
                                backend.embed_image_bytes.return_value = np.ones(2048, dtype=np.float32)
                                result = run_runtime_health_check(
                                    embedding_backend_factory=lambda: backend
                                )

        self.assertTrue(result.smoke_test_ok)
        self.assertEqual("Vulkan0: Test GPU", result.gpu_name)
        self.assertEqual("amd", result.gpu_vendor)
        self.assertEqual("0x1002", result.gpu_vendor_id)
        self.assertEqual(2048, result.image_smoke_vector_dim)
        self.assertEqual("image-embedding-smoke", result.diagnostic_steps[-1]["step"])
        backend.embed_text.assert_called_once()
        backend.embed_image_bytes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
