from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from memesort_worker.app_commands import (
    ImportRequestError,
    parse_import_start_request,
    start_import_batch,
)
from memesort_worker.import_contracts import IndexingPolicy
from memesort_worker.import_controller import (
    ImportBatchConflictError,
    ImportTerminalOutcome,
)
from memesort_worker.web_security import SESSION_COOKIE_NAME, SessionGate
from memesort_worker.webapp import create_app


ORIGIN_HOST = "127.0.0.1:8765"
ORIGIN = f"http://{ORIGIN_HOST}"
BOOTSTRAP_SECRET = "bootstrap-secret-value"
SESSION_TOKEN = "session-token-value"


def _call(app, method, path, *, headers=None, body=b""):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
        "HTTP_HOST": ORIGIN_HOST,
    }
    for key, value in (headers or {}).items():
        environ[key] = value
    captured: dict[str, object] = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = response_headers

    payload = b"".join(app(environ, start_response))
    return str(captured["status"]), payload


class StubRuntimeGate:
    def __init__(self, ready: bool, message: str) -> None:
        self.verdict = (ready, message)

    def is_ready_for_indexing(self) -> tuple[bool, str]:
        return self.verdict


class RecordingWorkerLoop:
    def __init__(self) -> None:
        self.resume_calls = 0

    def resume(self) -> None:
        self.resume_calls += 1


class StubImportController:
    def __init__(self) -> None:
        self.running = False
        self.starts: list[tuple[list[str], object]] = []

    def snapshot(self):
        return type(
            "Snapshot",
            (),
            {"running": self.running},
        )()

    def start(self, sources, on_terminal=None):
        self.starts.append((list(sources), on_terminal))
        return {"status": "scanning", "sources": list(sources)}


class _Snapshot:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class ParseImportStartRequestTests(unittest.TestCase):
    def test_canonical_payload_accepts_bounded_paths_and_one_policy(self) -> None:
        sources, policy = parse_import_start_request(
            {
                "sources": ["C:/Memes/a.png", "C:/Memes/b.png"],
                "indexing_policy": "if-ready",
            }
        )

        self.assertEqual(
            ["C:/Memes/a.png", "C:/Memes/b.png"],
            sources,
        )
        self.assertIs(IndexingPolicy.IF_READY, policy)

    def test_legacy_payload_uses_a_real_boolean_and_maps_it_to_policy(self) -> None:
        required_sources, required_policy = parse_import_start_request(
            {"path": "C:/Memes", "start_indexing": True}
        )
        never_sources, never_policy = parse_import_start_request(
            {"path": "C:/Memes", "start_indexing": False}
        )
        omitted_sources, omitted_policy = parse_import_start_request(
            {"path": "C:/Memes"}
        )

        self.assertEqual(["C:/Memes"], required_sources)
        self.assertIs(IndexingPolicy.REQUIRED, required_policy)
        self.assertEqual(["C:/Memes"], never_sources)
        self.assertIs(IndexingPolicy.NEVER, never_policy)
        self.assertEqual(["C:/Memes"], omitted_sources)
        self.assertIs(IndexingPolicy.NEVER, omitted_policy)

    def test_rejects_ambiguous_malformed_and_oversized_requests(self) -> None:
        invalid_payloads = (
            [],
            {},
            {"sources": ["a"], "path": "b", "indexing_policy": "never"},
            {"sources": "a", "indexing_policy": "never"},
            {"sources": [], "indexing_policy": "never"},
            {"sources": ["a"], "indexing_policy": "unknown"},
            {"sources": ["a"], "indexing_policy": True},
            {"sources": ["a"]},
            {"sources": ["a"], "indexing_policy": "never", "extra": True},
            {"path": "C:/Memes", "start_indexing": "true"},
            {"path": "C:/Memes", "start_indexing": 1},
            {"path": 1},
            {"path": "C:/Memes", "extra": True},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ImportRequestError):
                parse_import_start_request(payload)

        with patch("memesort_worker.app_commands.MAX_IMPORT_SOURCES", 2):
            with self.assertRaises(ImportRequestError):
                parse_import_start_request(
                    {
                        "sources": ["a", "b", "c"],
                        "indexing_policy": "never",
                    }
                )

        with patch("memesort_worker.app_commands.MAX_IMPORT_PATH_UTF8_BYTES", 4):
            with self.assertRaises(ImportRequestError):
                parse_import_start_request(
                    {
                        "sources": ["12345"],
                        "indexing_policy": "never",
                    }
                )

    def test_rejects_non_transport_safe_paths(self) -> None:
        for path in ("", "bad\npath", "bad\rpath", "bad\0path"):
            with self.subTest(path=path), self.assertRaises(ImportRequestError):
                parse_import_start_request(
                    {
                        "sources": [path],
                        "indexing_policy": "never",
                    }
                )


class StartImportBatchPolicyTests(unittest.TestCase):
    def _terminal(self, status: str, jobs_created: int = 3) -> ImportTerminalOutcome:
        return ImportTerminalOutcome(
            status=status,
            batch_id="batch-1",
            result=None,
            partial_result=None,
            error=None,
            jobs_created=jobs_created,
        )

    def test_required_rejects_unready_runtime_before_starting(self) -> None:
        controller = StubImportController()
        worker = RecordingWorkerLoop()

        with self.assertRaisesRegex(ValueError, "runtime is not ready"):
            start_import_batch(
                Path("C:/Library"),
                ["C:/Memes"],
                controller,
                worker,
                StubRuntimeGate(False, "runtime is not ready"),
                IndexingPolicy.REQUIRED,
            )

        self.assertEqual([], controller.starts)
        self.assertEqual(0, worker.resume_calls)

    def test_concurrent_start_returns_conflict_before_runtime_policy(self) -> None:
        controller = StubImportController()
        controller.running = True
        worker = RecordingWorkerLoop()

        with self.assertRaisesRegex(ImportBatchConflictError, "already running"):
            start_import_batch(
                Path("C:/Library"),
                ["C:/Memes"],
                controller,
                worker,
                StubRuntimeGate(False, "runtime is not ready"),
                IndexingPolicy.REQUIRED,
            )

        self.assertEqual([], controller.starts)
        self.assertEqual(0, worker.resume_calls)

    def test_if_ready_imports_without_authorization_and_does_not_wake(self) -> None:
        controller = StubImportController()
        worker = RecordingWorkerLoop()

        start_import_batch(
            Path("C:/Library"),
            ["C:/Memes"],
            controller,
            worker,
            StubRuntimeGate(False, "runtime is not ready"),
            IndexingPolicy.IF_READY,
        )

        self.assertEqual([["C:/Memes"]], [start[0] for start in controller.starts])
        self.assertIsNone(controller.starts[0][1])
        self.assertEqual(0, worker.resume_calls)

    def test_authorized_required_wakes_only_when_jobs_were_created(self) -> None:
        controller = StubImportController()
        worker = RecordingWorkerLoop()

        start_import_batch(
            Path("C:/Library"),
            ["C:/Memes"],
            controller,
            worker,
            StubRuntimeGate(True, "ready"),
            IndexingPolicy.REQUIRED,
        )
        on_terminal = controller.starts[0][1]
        self.assertIsNotNone(on_terminal)
        on_terminal(self._terminal("completed", jobs_created=3))
        self.assertEqual(1, worker.resume_calls)

        worker.resume_calls = 0
        on_terminal(self._terminal("completed", jobs_created=0))
        self.assertEqual(0, worker.resume_calls)

    def test_authorized_if_ready_wakes_after_terminal_result_kinds(self) -> None:
        for status in ("completed", "completed_with_errors", "failed"):
            with self.subTest(status=status):
                controller = StubImportController()
                worker = RecordingWorkerLoop()
                start_import_batch(
                    Path("C:/Library"),
                    ["C:/Memes"],
                    controller,
                    worker,
                    StubRuntimeGate(True, "ready"),
                    IndexingPolicy.IF_READY,
                )
                controller.starts[0][1](self._terminal(status, jobs_created=2))
                self.assertEqual(1, worker.resume_calls)

    def test_cancelled_batch_does_not_wake_worker(self) -> None:
        controller = StubImportController()
        worker = RecordingWorkerLoop()
        start_import_batch(
            Path("C:/Library"),
            ["C:/Memes"],
            controller,
            worker,
            StubRuntimeGate(True, "ready"),
            IndexingPolicy.IF_READY,
        )

        controller.starts[0][1](self._terminal("cancelled", jobs_created=2))

        self.assertEqual(0, worker.resume_calls)

    def test_never_skips_runtime_check_and_wake_up(self) -> None:
        class ThrowingRuntimeGate:
            def is_ready_for_indexing(self):
                raise AssertionError("never policy must not authorize runtime")

        controller = StubImportController()
        worker = RecordingWorkerLoop()

        start_import_batch(
            Path("C:/Library"),
            ["C:/Memes"],
            controller,
            worker,
            ThrowingRuntimeGate(),
            IndexingPolicy.NEVER,
        )

        self.assertEqual([["C:/Memes"]], [start[0] for start in controller.starts])
        self.assertIsNone(controller.starts[0][1])
        self.assertEqual(0, worker.resume_calls)


class ImportStartEndpointTests(unittest.TestCase):
    def _make_app(self, temp_dir: str, **kwargs):
        gate = SessionGate(
            origin_host=ORIGIN_HOST,
            bootstrap_secret=BOOTSTRAP_SECRET,
            session_token=SESSION_TOKEN,
        )
        return create_app(
            str(Path(temp_dir) / "library"),
            security=gate,
            **kwargs,
        )

    def _session_headers(self):
        return {"HTTP_COOKIE": f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"}

    def test_canonical_start_route_forwards_sources_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._make_app(temp_dir)
            try:
                with patch(
                    "memesort_worker.webapp.start_import_batch",
                    return_value=_Snapshot({"status": "scanning"}),
                ) as start_mock:
                    status, _body = _call(
                        app,
                        "POST",
                        "/api/import/start",
                        headers=self._session_headers(),
                        body=json.dumps(
                            {
                                "sources": ["C:/Memes/a.png", "C:/Memes/b.png"],
                                "indexing_policy": "if-ready",
                            }
                        ).encode("utf-8"),
                    )
                self.assertTrue(status.startswith("202 "))
                self.assertEqual(
                    ["C:/Memes/a.png", "C:/Memes/b.png"],
                    start_mock.call_args.args[1],
                )
                self.assertEqual(
                    IndexingPolicy.IF_READY,
                    start_mock.call_args.kwargs["indexing_policy"],
                )
            finally:
                app.shutdown()

    def test_ambiguous_start_route_returns_bad_request_without_starting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._make_app(temp_dir)
            try:
                with patch(
                    "memesort_worker.webapp.start_import_batch"
                ) as start_mock:
                    status, _body = _call(
                        app,
                        "POST",
                        "/api/import/start",
                        headers=self._session_headers(),
                        body=json.dumps(
                            {
                                "sources": ["C:/Memes/a.png"],
                                "path": "C:/Memes",
                                "indexing_policy": "if-ready",
                            }
                        ).encode("utf-8"),
                    )
                self.assertTrue(status.startswith("400 "))
                start_mock.assert_not_called()
            finally:
                app.shutdown()

    def test_legacy_string_boolean_is_rejected_before_starting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._make_app(temp_dir)
            try:
                with patch(
                    "memesort_worker.webapp.start_import_batch"
                ) as start_mock:
                    status, _body = _call(
                        app,
                        "POST",
                        "/api/import/start",
                        headers=self._session_headers(),
                        body=json.dumps(
                            {"path": "C:/Memes", "start_indexing": "true"}
                        ).encode("utf-8"),
                    )
                self.assertTrue(status.startswith("400 "))
                start_mock.assert_not_called()
            finally:
                app.shutdown()

    def test_concurrent_start_returns_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._make_app(temp_dir)
            try:
                with patch(
                    "memesort_worker.webapp.start_import_batch",
                    side_effect=ImportBatchConflictError(
                        "An Import Batch is already running or paused."
                    ),
                ) as start_mock:
                    status, body = _call(
                        app,
                        "POST",
                        "/api/import/start",
                        headers=self._session_headers(),
                        body=json.dumps(
                            {
                                "sources": ["C:/Memes/a.png"],
                                "indexing_policy": "if-ready",
                            }
                        ).encode("utf-8"),
                    )
                self.assertTrue(status.startswith("409 "))
                self.assertIn(b"already running", body)
                self.assertEqual(1, start_mock.call_count)
            finally:
                app.shutdown()

    def test_oversized_start_request_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._make_app(temp_dir, max_body_bytes=16)
            try:
                status, _body = _call(
                    app,
                    "POST",
                    "/api/import/start",
                    headers=self._session_headers(),
                    body=json.dumps(
                        {
                            "sources": ["C:/Memes/a.png"],
                            "indexing_policy": "if-ready",
                        }
                    ).encode("utf-8"),
                )
                self.assertTrue(status.startswith("413 "))
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
