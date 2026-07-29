"""Lifecycle tests for the instance-owned Pinned Runtime."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from memesort_worker.library import RuntimeHealthResult, initialize_library
from memesort_worker.local_app_host import (
    LocalAppHost,
    LocalAppHostConfig,
    STATE_STOPPED,
)
from memesort_worker.pinned_runtime import PinnedRuntime, PinnedRuntimeClosedError
from memesort_worker.runtime_manifest import load_runtime_manifest
from memesort_worker.runtime_service import (
    RuntimeAuthorizationError,
    get_current_health_check,
    get_last_health_check,
)
from memesort_worker.webapp import create_app


def _health_result(smoke_test_ok: bool, fingerprint: str | None = None) -> RuntimeHealthResult:
    return RuntimeHealthResult(
        runtime_fingerprint=fingerprint or load_runtime_manifest().runtime_fingerprint,
        backend_name="llama.cpp",
        device="Vulkan0",
        gpu_name="Test GPU",
        gpu_vendor=None,
        gpu_vendor_id=None,
        text_smoke_vector_dim=2048 if smoke_test_ok else None,
        image_smoke_vector_dim=2048 if smoke_test_ok else None,
        diagnostic_steps=[],
        smoke_test_ok=smoke_test_ok,
        error=None if smoke_test_ok else "Vulkan0 is unavailable.",
    )


class PinnedRuntimeTests(unittest.TestCase):
    def _runtime(self, library_root: Path) -> PinnedRuntime:
        runtime = PinnedRuntime(library_root)
        self.addCleanup(runtime.close)
        return runtime

    def test_authorize_stores_health_on_the_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime(Path(temp_dir) / "library")
            with patch(
                "memesort_worker.pinned_runtime.run_runtime_health_check",
                return_value=_health_result(True),
            ):
                result = runtime.authorize()

        self.assertTrue(result.smoke_test_ok)
        self.assertIs(result, runtime.current_health_check())

    def test_authorize_failure_raises_and_keeps_failed_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime(Path(temp_dir) / "library")
            with patch(
                "memesort_worker.pinned_runtime.run_runtime_health_check",
                return_value=_health_result(False),
            ):
                with self.assertRaisesRegex(RuntimeAuthorizationError, "Vulkan0 is unavailable"):
                    runtime.authorize()

            ready, detail = runtime.is_ready_for_indexing()

        self.assertFalse(runtime.current_health_check().smoke_test_ok)
        self.assertFalse(ready)

    def test_health_on_one_runtime_does_not_authorize_another(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_a = self._runtime(root / "library-a")
            runtime_b = self._runtime(root / "library-b")
            with patch(
                "memesort_worker.pinned_runtime.run_runtime_health_check",
                return_value=_health_result(True),
            ):
                runtime_a.authorize()

            ready_b, detail_b = runtime_b.is_ready_for_indexing()

        self.assertIsNotNone(runtime_a.current_health_check())
        self.assertIsNone(runtime_b.current_health_check())
        self.assertFalse(ready_b)

    def test_stale_manifest_fingerprint_is_not_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._runtime(Path(temp_dir) / "library")
            with patch(
                "memesort_worker.pinned_runtime.run_runtime_health_check",
                return_value=_health_result(True, fingerprint="stale-fingerprint"),
            ):
                runtime.authorize()

            ready, detail = runtime.is_ready_for_indexing()
            manifest = load_runtime_manifest()

        self.assertFalse(ready)
        if manifest.llama_server_path.is_file() and manifest.main_model_path.is_file():
            self.assertIn("stale", detail)

    def test_instance_health_check_does_not_write_global_session_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            initialize_library(library_root)
            missing_manifest = replace(
                load_runtime_manifest(),
                source_path=Path(temp_dir) / "runtime-manifest.json",
            )
            runtime = self._runtime(library_root)
            with patch(
                "memesort_worker.runtime_service.load_runtime_manifest",
                return_value=missing_manifest,
            ):
                result = runtime.run_health_check()

            session_health = get_current_health_check(library_root)
            persisted = get_last_health_check(library_root)

        self.assertFalse(result.smoke_test_ok)
        self.assertIs(result, runtime.current_health_check())
        self.assertIsNone(session_health)
        self.assertIsNotNone(persisted)

    def test_create_app_rejects_a_runtime_bound_to_another_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = self._runtime(root / "library-a")
            with self.assertRaisesRegex(ValueError, "library root"):
                create_app(str(root / "library-b"), runtime=runtime)

    def test_close_is_idempotent_and_closes_the_owned_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = PinnedRuntime(Path(temp_dir) / "library")
            backend = Mock()
            with patch(
                "memesort_worker.embedding_backend.LlamaCppEmbeddingBackend",
                return_value=backend,
            ):
                self.assertIs(backend, runtime.get_embedding_backend())
            runtime.close()
            runtime.close()

        self.assertEqual(1, backend.close.call_count)
        self.assertTrue(runtime.closed)
        self.assertIsNone(runtime.current_health_check())
        ready, detail = runtime.is_ready_for_indexing()
        self.assertFalse(ready)
        self.assertIn("closed", detail)
        with self.assertRaises(PinnedRuntimeClosedError):
            runtime.run_health_check()

    def test_live_runtimes_share_one_backend_and_last_close_tears_it_down(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = PinnedRuntime(root / "library-a")
            second = PinnedRuntime(root / "library-b")
            backend = Mock()
            with patch(
                "memesort_worker.embedding_backend.LlamaCppEmbeddingBackend",
                return_value=backend,
            ) as backend_class:
                self.assertIs(backend, first.get_embedding_backend())
                self.assertIs(backend, second.get_embedding_backend())
            self.assertEqual(1, backend_class.call_count)
            self.assertIs(first.scheduler, second.scheduler)
            first.close()
            self.assertEqual(0, backend.close.call_count)
            second.close()
            self.assertEqual(1, backend.close.call_count)

        self.assertTrue(first.closed)
        self.assertTrue(second.closed)


class LocalAppHostRuntimeLifecycleTests(unittest.TestCase):
    def _config(self, library_root: Path, authorize: bool = False) -> LocalAppHostConfig:
        return LocalAppHostConfig(
            library_root=library_root,
            authorize_runtime=authorize,
            static_root=Path(__file__).resolve().parents[1]
            / "memesort_worker"
            / "web_static",
        )

    def test_stop_closes_the_runtime_instance_in_documented_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(self._config(Path(temp_dir) / "library"))
            host.start()
            runtime = host._runtime
            report = host.stop()

        self.assertTrue(runtime.closed)
        self.assertEqual(
            [
                "refuse-new-work",
                "stop-http-server",
                "close-http-socket",
                "stop-workers",
                "close-runtime",
                "join-server-thread",
            ],
            [step.name for step in report.steps],
        )

    def test_stop_is_idempotent_for_the_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(self._config(Path(temp_dir) / "library"))
            host.start()
            first = host.stop()
            second = host.stop()

        self.assertTrue(first.clean)
        self.assertEqual((), second.steps)
        self.assertEqual(STATE_STOPPED, host.state)

    def test_failed_start_closes_the_runtime_it_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(self._config(Path(temp_dir) / "library"))
            with patch(
                "memesort_worker.local_app_host.make_server",
                side_effect=OSError("port unavailable"),
            ):
                with self.assertRaises(OSError):
                    host.start()

        self.assertEqual(STATE_STOPPED, host.state)
        self.assertIsNotNone(host._runtime)
        self.assertTrue(host._runtime.closed)

    def test_authorization_failure_is_reported_but_host_still_serves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            host = LocalAppHost(
                self._config(Path(temp_dir) / "library", authorize=True)
            )
            with patch(
                "memesort_worker.pinned_runtime.run_runtime_health_check",
                return_value=_health_result(False),
            ):
                host.start()
            try:
                self.assertEqual("running", host.state)
            finally:
                report = host.stop()

        self.assertEqual("Vulkan0 is unavailable.", report.authorization_error)

    def test_independent_hosts_own_independent_runtimes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            host_a = LocalAppHost(self._config(root / "library-a"))
            host_b = LocalAppHost(self._config(root / "library-b"))
            host_a.start()
            host_b.start()
            try:
                self.assertIsNot(host_a._runtime, host_b._runtime)
                with patch(
                    "memesort_worker.pinned_runtime.run_runtime_health_check",
                    return_value=_health_result(True),
                ):
                    host_a._runtime.authorize()
                self.assertIsNone(host_b._runtime.current_health_check())
            finally:
                host_a.stop()
                host_b.stop()

        self.assertTrue(host_a._runtime.closed)
        self.assertTrue(host_b._runtime.closed)


if __name__ == "__main__":
    unittest.main()
