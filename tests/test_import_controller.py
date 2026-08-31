from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from memesort_worker.import_contracts import (
    ImportBatchError,
    ImportBatchErrorCode,
    ImportBatchResult,
    ImportFailure,
    ImportFailureCode,
    ImportFailureStage,
    ImportProgress,
)
from memesort_worker.import_controller import (
    ImportBatchConflictError,
    ImportController,
    ImportSnapshot,
)


def make_result(**overrides: object) -> ImportBatchResult:
    values: dict[str, object] = {
        "library_root": "C:/Library",
        "selected_sources": 1,
        "effective_sources": 1,
        "discovered_files": 1,
        "supported_files": 1,
        "unsupported_files": 0,
        "reparse_points_skipped": 0,
        "scan_failures": 0,
        "processed_files": 1,
        "succeeded_files": 1,
        "failed_files": 0,
        "new_assets": 1,
        "duplicate_assets": 0,
        "source_records_added": 1,
        "source_records_refreshed": 0,
        "jobs_created": 1,
        "failure_details": (),
        "active_recipe_id": None,
    }
    values.update(overrides)
    return ImportBatchResult(**values)


def make_progress(
    *,
    phase: str = "scanning",
    source_name: str | None = None,
    discovered_files: int = 0,
    supported_files: int = 0,
    effective_sources: int = 1,
) -> ImportProgress:
    return ImportProgress(
        phase=phase,
        current_source_name=source_name,
        selected_sources=1,
        effective_sources=effective_sources,
        discovered_files=discovered_files,
        supported_files=supported_files,
        unsupported_files=discovered_files - supported_files,
        reparse_points_skipped=0,
        scan_failures=0,
        processed_files=0,
        succeeded_files=0,
        failed_files=0,
        new_assets=0,
        duplicate_assets=0,
        source_records_added=0,
        source_records_refreshed=0,
        jobs_created=0,
        failure_details=(),
    )


class ImportControllerTests(unittest.TestCase):
    def _wait_for_snapshot(
        self,
        controller: ImportController,
        predicate,
        *,
        timeout: float = 3.0,
    ) -> ImportSnapshot:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = controller.snapshot()
            if predicate(snapshot):
                return snapshot
            time.sleep(0.005)
        self.fail(
            f"Timed out waiting for ImportController snapshot: "
            f"{controller.snapshot().to_dict()}"
        )

    def test_each_start_gets_a_unique_batch_id_retained_after_terminal(self) -> None:
        first_result = make_result()
        second_result = make_result()

        def runner(_root, _sources, _wait, _progress):
            return first_result

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        first_start = controller.start([Path("C:/Memes/first.png")])
        first_done = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )

        controller_with_second_result = ImportController(
            Path(tempfile.gettempdir()),
            lambda _root, _sources, _wait, _progress: second_result,
        )
        second_start = controller_with_second_result.start(
            [Path("C:/Memes/second.png")]
        )
        second_done = self._wait_for_snapshot(
            controller_with_second_result,
            lambda snapshot: snapshot.status == "completed",
        )

        self.assertIsNotNone(first_start.batch_id)
        self.assertIsNotNone(second_start.batch_id)
        self.assertNotEqual(first_start.batch_id, second_start.batch_id)
        self.assertEqual(first_start.batch_id, first_done.batch_id)
        self.assertEqual(second_start.batch_id, second_done.batch_id)

    def test_progress_exposes_basename_and_state_transitions(self) -> None:
        allow_import = threading.Event()
        allow_finish = threading.Event()

        def runner(_root, _sources, _wait, progress):
            progress(
                make_progress(
                    phase="scanning",
                    source_name="C:/Memes/nested.png",
                    discovered_files=1,
                    supported_files=1,
                )
            )
            allow_import.wait(timeout=3)
            progress(
                make_progress(
                    phase="importing",
                    source_name="C:/Memes/nested.png",
                    discovered_files=1,
                    supported_files=1,
                )
            )
            allow_finish.wait(timeout=3)
            return make_result()

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start([Path("C:/Memes/nested.png")])
        scanning = self._wait_for_snapshot(
            controller,
            lambda snapshot: (
                snapshot.status == "scanning"
                and snapshot.current_source_name == "nested.png"
            ),
        )
        allow_import.set()
        importing = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "importing",
        )
        allow_finish.set()
        terminal = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )

        self.assertEqual("nested.png", scanning.current_source_name)
        self.assertEqual(1, scanning.discovered_files)
        self.assertEqual(1, scanning.supported_files)
        self.assertEqual("nested.png", importing.current_source_name)
        self.assertEqual(1, importing.discovered_files)
        self.assertEqual(1, importing.supported_files)
        self.assertEqual(terminal.batch_id, scanning.batch_id)
        self.assertEqual(terminal.batch_id, importing.batch_id)

    def test_second_start_is_rejected_while_a_batch_is_active(self) -> None:
        release = threading.Event()

        def runner(_root, _sources, _wait, _progress):
            release.wait(timeout=3)
            return make_result()

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start([Path("C:/Memes/blocked.png")])
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.running,
        )

        with self.assertRaisesRegex(ImportBatchConflictError, "already running or paused"):
            controller.start([Path("C:/Memes/second.png")])

        release.set()
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )

    def test_second_start_is_rejected_while_paused(self) -> None:
        ready_to_wait = threading.Event()
        allow_wait = threading.Event()

        def runner(_root, _sources, wait, _progress):
            ready_to_wait.set()
            if not allow_wait.wait(timeout=3):
                raise AssertionError("Timed out waiting to enter pause test")
            wait()
            return make_result()

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start([Path("C:/Memes/paused.png")])
        self.assertTrue(ready_to_wait.wait(timeout=3))
        controller.pause()
        allow_wait.set()
        paused = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.paused,
        )
        self.assertEqual("paused", paused.status)

        with self.assertRaisesRegex(ImportBatchConflictError, "already running or paused"):
            controller.start([Path("C:/Memes/second.png")])

        controller.resume()
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )

    def test_pause_request_during_file_processing_finishes_before_paused(self) -> None:
        processing_started = threading.Event()
        allow_finish = threading.Event()

        def runner(_root, _sources, wait, progress):
            progress(
                make_progress(
                    phase="scanning",
                    source_name="busy.png",
                    discovered_files=1,
                    supported_files=1,
                )
            )
            wait()
            processing_started.set()
            allow_finish.wait(timeout=3)
            progress(
                make_progress(
                    phase="importing",
                    source_name="busy.png",
                    discovered_files=1,
                    supported_files=1,
                )
            )
            return make_result()

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start([Path("C:/Memes/busy.png")])
        self.assertTrue(processing_started.wait(timeout=3))

        controller.pause()
        pausing = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "pausing",
        )
        self.assertFalse(pausing.paused)
        self.assertTrue(pausing.pause_requested)

        allow_finish.set()
        terminal = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )
        self.assertFalse(terminal.paused)

    def test_unsupported_only_batch_completes_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "notes.txt"
            source.write_text("not an image", encoding="utf-8")

            controller = ImportController(root / "library")
            controller.start([source])
            terminal = self._wait_for_snapshot(
                controller,
                lambda snapshot: snapshot.status == "completed",
            )

        self.assertEqual(0, terminal.result["new_assets"])
        self.assertEqual(1, terminal.result["unsupported_files"])
        self.assertEqual(0, terminal.result["failure_count"])

    def test_file_failure_produces_completed_with_errors_and_preserves_assets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good_source = root / "good.png"
            good_source.write_bytes(b"not a real image")
            bad_source = root / "bad.png"
            bad_source.write_bytes(b"not a real image")

            controller = ImportController(root / "library")
            controller.start([good_source, bad_source])
            terminal = self._wait_for_snapshot(
                controller,
                lambda snapshot: snapshot.status == "completed_with_errors",
            )

        self.assertEqual("completed_with_errors", terminal.status)
        self.assertIsNone(terminal.partial_result)
        self.assertEqual(2, terminal.result["processed_files"])
        self.assertEqual(0, terminal.result["succeeded_files"])
        self.assertEqual(2, terminal.result["failed_files"])
        self.assertEqual(2, terminal.result["failure_count"])

    def test_fatal_error_preserves_partial_result(self) -> None:
        partial_result = make_result(
            processed_files=0,
            succeeded_files=0,
            failed_files=0,
            new_assets=0,
            duplicate_assets=0,
            source_records_added=0,
            source_records_refreshed=0,
            jobs_created=0,
        )

        def runner(_root, _sources, _wait, _progress):
            raise ImportBatchError(
                code=ImportBatchErrorCode.FILE_LIMIT_EXCEEDED,
                detail="Discovery limit reached.",
                partial_result=partial_result,
            )

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start([Path("C:/Memes/large.png")])
        terminal = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "failed",
        )

        self.assertIsNone(terminal.result)
        self.assertIsNotNone(terminal.partial_result)
        self.assertEqual(
            ImportBatchErrorCode.FILE_LIMIT_EXCEEDED.value,
            terminal.error["code"],
        )
        self.assertEqual(0, terminal.partial_result["processed_files"])

    def test_shutdown_produces_cancelled_with_committed_counts(self) -> None:
        allow_wait = threading.Event()

        def runner(_root, _sources, wait, progress):
            progress(
                make_progress(
                    phase="scanning",
                    source_name="shutdown.png",
                    discovered_files=2,
                    supported_files=2,
                )
            )
            allow_wait.wait(timeout=3)
            wait()
            return make_result()

        terminal_calls: list[str] = []
        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start(
            [Path("C:/Memes/shutdown.png")],
            on_terminal=lambda outcome: terminal_calls.append(outcome.status),
        )
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.effective_sources == 1,
        )
        controller.pause()
        allow_wait.set()
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.paused,
        )

        controller.shutdown()
        terminal = self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "cancelled",
        )

        self.assertEqual(["cancelled"], terminal_calls)
        self.assertIsNotNone(terminal.partial_result)
        self.assertEqual(2, terminal.partial_result["discovered_files"])
        self.assertEqual(2, terminal.partial_result["supported_files"])

        with self.assertRaisesRegex(RuntimeError, "shut down"):
            controller.start([Path("C:/Memes/after-shutdown.png")])

    def test_terminal_callback_runs_exactly_once(self) -> None:
        calls: list[str] = []

        def runner(_root, _sources, _wait, progress):
            progress(
                make_progress(
                    phase="importing",
                    source_name="callback.png",
                    discovered_files=1,
                    supported_files=1,
                )
            )
            return make_result()

        controller = ImportController(Path(tempfile.gettempdir()), runner)
        controller.start(
            [Path("C:/Memes/callback.png")],
            on_terminal=lambda outcome: calls.append(outcome.status),
        )
        self._wait_for_snapshot(
            controller,
            lambda snapshot: snapshot.status == "completed",
        )
        time.sleep(0.05)

        self.assertEqual(["completed"], calls)


if __name__ == "__main__":
    unittest.main()
