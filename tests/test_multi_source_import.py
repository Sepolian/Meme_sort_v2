from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from memesort_worker.library import (
    ImportBatchError,
    ImportBatchErrorCode,
    ImportBatchPreflightError,
    ImportFailureCode,
    ImportFailureStage,
    import_sources,
    list_assets,
)
from memesort_worker import asset_catalog


class _ReparseMetadata:
    def __init__(self, metadata: object) -> None:
        self._metadata = metadata
        self.st_file_attributes = 0x400

    def __getattr__(self, name: str) -> object:
        return getattr(self._metadata, name)


class _CommitOutcomeUncertainConnection:
    """Report one commit failure after SQLite has durably committed it."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._reported_uncertain_commit = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)

    def __enter__(self) -> "_CommitOutcomeUncertainConnection":
        self._connection.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        suppress = self._connection.__exit__(exc_type, exc, traceback)
        if exc_type is None and not self._reported_uncertain_commit:
            self._reported_uncertain_commit = True
            raise sqlite3.OperationalError("commit acknowledgement was lost")
        return suppress


class MultiSourceImportTests(unittest.TestCase):
    def _write_image(
        self,
        path: Path,
        color: tuple[int, int, int],
    ) -> None:
        Image.new("RGB", (40, 30), color).save(path, format="PNG")

    def test_imports_multiple_explicit_files_as_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            self._write_image(first_source, (255, 0, 0))
            self._write_image(second_source, (0, 0, 255))

            result = import_sources(
                root / "library",
                [first_source, second_source],
            )
            assets = list_assets(root / "library")

        self.assertEqual(2, result.selected_sources)
        self.assertEqual(2, result.effective_sources)
        self.assertEqual(2, result.discovered_files)
        self.assertEqual(2, result.supported_files)
        self.assertEqual(0, result.unsupported_files)
        self.assertEqual(2, result.processed_files)
        self.assertEqual(2, result.succeeded_files)
        self.assertEqual(0, result.failed_files)
        self.assertEqual(2, result.new_assets)
        self.assertEqual(0, result.duplicate_assets)
        self.assertEqual(2, result.source_records_added)
        self.assertEqual(0, result.source_records_refreshed)
        self.assertEqual(6, result.jobs_created)
        self.assertEqual(2, len(assets.assets))

    def test_imports_a_mixed_file_and_directory_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            nested_directory = source_directory / "nested"
            source_directory.mkdir()
            nested_directory.mkdir()
            first_source = source_directory / "first.png"
            second_source = nested_directory / "second.png"
            explicit_source = root / "explicit.png"
            self._write_image(first_source, (255, 0, 0))
            self._write_image(second_source, (0, 0, 255))
            self._write_image(explicit_source, (0, 255, 0))

            result = import_sources(
                root / "library",
                [source_directory, explicit_source],
            )
            assets = list_assets(root / "library")

        self.assertEqual(2, result.selected_sources)
        self.assertEqual(2, result.effective_sources)
        self.assertEqual(3, result.discovered_files)
        self.assertEqual(3, result.supported_files)
        self.assertEqual(3, result.processed_files)
        self.assertEqual(3, result.new_assets)
        self.assertEqual(3, len(assets.assets))

    def test_multi_directory_scan_progress_is_batch_cumulative(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_directory = root / "first"
            second_directory = root / "second"
            first_directory.mkdir()
            second_directory.mkdir()
            self._write_image(first_directory / "one.png", (255, 0, 0))
            self._write_image(first_directory / "two.png", (0, 255, 0))
            self._write_image(second_directory / "three.png", (0, 0, 255))
            scan_progress = []

            result = import_sources(
                root / "library",
                [first_directory, second_directory],
                progress_callback=lambda progress: (
                    scan_progress.append(progress)
                    if progress.phase == "scanning"
                    else None
                ),
            )

        discovered_counts = [progress.discovered_files for progress in scan_progress]
        supported_counts = [progress.supported_files for progress in scan_progress]
        self.assertEqual(sorted(discovered_counts), discovered_counts)
        self.assertEqual(sorted(supported_counts), supported_counts)
        self.assertEqual(result.discovered_files, discovered_counts[-1])
        self.assertEqual(result.supported_files, supported_counts[-1])

    def test_continues_after_a_corrupt_image_and_removes_its_library_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid_source = root / "valid.png"
            corrupt_source = root / "corrupt.png"
            library_root = root / "library"
            self._write_image(valid_source, (255, 0, 0))
            corrupt_source.write_bytes(b"not an image")

            result = import_sources(
                library_root,
                [valid_source, corrupt_source],
            )
            assets = list_assets(library_root)
            originals = list((library_root / "originals").iterdir())

        self.assertEqual(2, result.processed_files)
        self.assertEqual(1, result.succeeded_files)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(1, len(originals))
        self.assertEqual(ImportFailureStage.VALIDATION, result.failure_details[0].stage)
        self.assertEqual(
            ImportFailureCode.IMAGE_DECODE_FAILED,
            result.failure_details[0].code,
        )

    def test_rejects_a_corrupt_duplicate_from_a_legacy_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            corrupt_source = root / "corrupt.png"
            corrupt_source.write_bytes(b"not an image")
            asset_catalog.initialize_library(library_root)
            legacy_copy = library_root / "originals" / "legacy.png"
            legacy_copy.write_bytes(corrupt_source.read_bytes())
            content_hash = asset_catalog.compute_sha256(corrupt_source)
            conn = asset_catalog.connect(asset_catalog.database_path(library_root))
            try:
                with conn:
                    now = asset_catalog.utc_now()
                    conn.execute(
                        """
                        INSERT INTO asset (
                            id, library_path, media_type, content_hash, byte_size,
                            width, height, imported_at, updated_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "legacy-asset",
                            "originals/legacy.png",
                            "image/png",
                            content_hash,
                            legacy_copy.stat().st_size,
                            None,
                            None,
                            now,
                            now,
                            None,
                        ),
                    )
            finally:
                conn.close()

            result = import_sources(library_root, [corrupt_source])

        self.assertEqual(1, result.processed_files)
        self.assertEqual(0, result.duplicate_assets)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(
            ImportFailureCode.IMAGE_DECODE_FAILED,
            result.failure_details[0].code,
        )

    def test_retries_temporary_copy_cleanup_without_aborting_later_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            changing_source = root / "changing.png"
            valid_source = root / "valid.png"
            self._write_image(changing_source, (0, 0, 255))
            self._write_image(valid_source, (255, 0, 0))
            original_unlink = asset_catalog.Path.unlink
            original_copy2 = asset_catalog.shutil.copy2
            cleanup_lock_reported = False

            def copy2_with_changed_first_copy(
                source_path: object,
                target_path: object,
            ) -> str:
                copied = original_copy2(source_path, target_path)
                if Path(source_path) == changing_source:
                    Path(target_path).write_bytes(b"changed during copy")
                return copied

            def unlink_with_one_transient_lock(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal cleanup_lock_reported
                if (
                    not cleanup_lock_reported
                    and path.name.endswith(".tmp")
                    and path.exists()
                ):
                    cleanup_lock_reported = True
                    raise PermissionError("copy is temporarily locked")
                original_unlink(path, *args, **kwargs)

            with (
                patch.object(
                    asset_catalog.Path,
                    "unlink",
                    new=unlink_with_one_transient_lock,
                ),
                patch.object(
                    asset_catalog.shutil,
                    "copy2",
                    side_effect=copy2_with_changed_first_copy,
                ),
            ):
                result = import_sources(
                    root / "library",
                    [changing_source, valid_source],
                )
            originals = list((root / "library" / "originals").iterdir())

        self.assertTrue(cleanup_lock_reported)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, len(originals))

    def test_rejects_an_oversized_temporary_library_copy_without_retaining_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            original_copy2 = asset_catalog.shutil.copy2

            def copy2_that_exceeds_the_limit(source_path: object, target_path: object) -> str:
                copied = original_copy2(source_path, target_path)
                Path(target_path).write_bytes(b"x" * 100_001)
                return copied

            with (
                patch.object(asset_catalog, "MAX_IMPORT_SOURCE_BYTES", 100_000),
                patch.object(
                    asset_catalog.shutil,
                    "copy2",
                    side_effect=copy2_that_exceeds_the_limit,
                ),
            ):
                result = import_sources(library_root, [source])
            originals = list((library_root / "originals").iterdir())

        self.assertEqual(1, result.processed_files)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(0, result.new_assets)
        self.assertEqual([], originals)
        self.assertEqual(
            ImportFailureCode.LIBRARY_COPY_TOO_LARGE,
            result.failure_details[0].code,
        )

    def test_continues_when_a_supported_source_disappears_before_processing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid_source = root / "valid.png"
            disappeared_source = root / "disappeared.png"
            library_root = root / "library"
            self._write_image(valid_source, (255, 0, 0))
            self._write_image(disappeared_source, (0, 0, 255))
            permission_checks = 0

            def remove_the_second_candidate() -> None:
                nonlocal permission_checks
                permission_checks += 1
                if permission_checks == 2:
                    disappeared_source.unlink()

            result = import_sources(
                library_root,
                [valid_source, disappeared_source],
                wait_for_permission=remove_the_second_candidate,
            )
            assets = list_assets(library_root)

        self.assertEqual(2, result.processed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(
            ImportFailureCode.SOURCE_MISSING,
            result.failure_details[0].code,
        )

    def test_rechecks_supported_candidate_safety_at_processing_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            processing_started = False
            original_lstat = asset_catalog.os.lstat

            def lstat_with_late_reparse(
                path: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                metadata = original_lstat(path, *args, **kwargs)
                if processing_started and Path(path) == source:
                    return _ReparseMetadata(metadata)
                return metadata

            def mark_processing_started() -> None:
                nonlocal processing_started
                processing_started = True

            with patch.object(
                asset_catalog.os,
                "lstat",
                side_effect=lstat_with_late_reparse,
            ):
                result = import_sources(
                    library_root,
                    [source],
                    wait_for_permission=mark_processing_started,
                )

        self.assertEqual(1, result.processed_files)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(
            ImportFailureCode.SOURCE_REPARSE_POINT,
            result.failure_details[0].code,
        )

    def test_rejects_source_files_over_the_import_size_limit_individually(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oversized_source = root / "oversized.png"
            valid_source = root / "valid.png"
            self._write_image(valid_source, (255, 0, 0))
            oversized_source.write_bytes(b"x" * 100_001)

            with patch.object(asset_catalog, "MAX_IMPORT_SOURCE_BYTES", 100_000):
                result = import_sources(
                    root / "library",
                    [valid_source, oversized_source],
                )

        self.assertEqual(2, result.processed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, result.failed_files)
        self.assertEqual(
            ImportFailureCode.SOURCE_TOO_LARGE,
            result.failure_details[0].code,
        )

    def test_rejects_a_frame_that_exceeds_the_pixel_limit_individually(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large-frame.png"
            self._write_image(source, (255, 0, 0))

            with patch.object(asset_catalog, "MAX_IMPORT_FRAME_PIXELS", 1_000):
                result = import_sources(root / "library", [source])

        self.assertEqual(1, result.failed_files)
        self.assertEqual(
            ImportFailureCode.IMAGE_FRAME_TOO_LARGE,
            result.failure_details[0].code,
        )

    def test_rejects_gifs_that_exceed_the_frame_limit_individually(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "animated.gif"
            first_frame = Image.new("RGB", (40, 30), (255, 0, 0))
            second_frame = Image.new("RGB", (40, 30), (0, 0, 255))
            first_frame.save(
                source,
                format="GIF",
                save_all=True,
                append_images=[second_frame],
            )

            with patch.object(asset_catalog, "MAX_IMPORT_GIF_FRAMES", 1):
                result = import_sources(root / "library", [source])

        self.assertEqual(1, result.failed_files)
        self.assertEqual(
            ImportFailureCode.GIF_FRAME_LIMIT_EXCEEDED,
            result.failure_details[0].code,
        )

    def test_caps_processing_failure_details_without_losing_the_failure_count(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = []
            for number in range(101):
                source = root / f"corrupt-{number}.png"
                source.write_bytes(b"not an image")
                sources.append(source)

            result = import_sources(root / "library", sources)

        self.assertEqual(101, result.failed_files)
        self.assertEqual(101, result.failure_count)
        self.assertEqual(100, len(result.failure_details))
        self.assertTrue(result.failures_truncated)

    def test_removes_a_final_library_copy_when_cataloging_it_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))

            with patch.object(
                asset_catalog.job_queue,
                "enqueue_thumbnail",
                side_effect=sqlite3.OperationalError("database unavailable"),
            ):
                result = import_sources(library_root, [source])
            assets = list_assets(library_root)
            originals = list((library_root / "originals").iterdir())

        self.assertEqual(1, result.failed_files)
        self.assertEqual(0, result.new_assets)
        self.assertEqual([], assets.assets)
        self.assertEqual([], originals)
        self.assertEqual(
            ImportFailureCode.DATABASE_WRITE_FAILED,
            result.failure_details[0].code,
        )

    def test_keeps_a_referenced_library_copy_when_commit_acknowledgement_is_lost(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            asset_catalog.initialize_library(library_root)
            original_connect = asset_catalog.connect
            connection_count = 0

            def connect_with_uncertain_import_commit(
                database_file: Path,
            ) -> sqlite3.Connection | _CommitOutcomeUncertainConnection:
                nonlocal connection_count
                connection_count += 1
                connection = original_connect(database_file)
                if connection_count == 2:
                    return _CommitOutcomeUncertainConnection(connection)
                return connection

            with patch.object(
                asset_catalog,
                "connect",
                side_effect=connect_with_uncertain_import_commit,
            ):
                result = import_sources(library_root, [source])
            assets = list_assets(library_root)
            originals = list((library_root / "originals").iterdir())
            retained_copy_is_file = len(originals) == 1 and originals[0].is_file()

        self.assertEqual(1, result.new_assets)
        self.assertEqual(0, result.failed_files)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(1, len(originals))
        self.assertTrue(retained_copy_is_file)

    def test_concurrent_equal_content_imports_reuse_the_winning_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            library_root = root / "library"
            self._write_image(first_source, (255, 0, 0))
            second_source.write_bytes(first_source.read_bytes())
            asset_catalog.initialize_library(library_root)
            ready_to_finalize = threading.Barrier(2)
            original_replace = asset_catalog.os.replace

            def replace_after_both_candidates_are_verified(
                source_path: object,
                destination_path: object,
            ) -> None:
                if Path(destination_path).parent == library_root / "originals":
                    ready_to_finalize.wait(timeout=5)
                original_replace(source_path, destination_path)

            with (
                patch.object(
                    asset_catalog.os,
                    "replace",
                    side_effect=replace_after_both_candidates_are_verified,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                first_future = executor.submit(
                    import_sources,
                    library_root,
                    [first_source],
                )
                second_future = executor.submit(
                    import_sources,
                    library_root,
                    [second_source],
                )
                results = [first_future.result(), second_future.result()]

            assets = list_assets(library_root)
            originals = list((library_root / "originals").iterdir())

        self.assertEqual(1, sum(result.new_assets for result in results))
        self.assertEqual(1, sum(result.duplicate_assets for result in results))
        self.assertEqual(0, sum(result.failed_files for result in results))
        self.assertEqual(2, sum(result.source_records_added for result in results))
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(2, assets.assets[0]["source_record_count"])
        self.assertEqual(1, len(originals))

    def test_concurrent_imports_of_one_source_count_one_add_and_one_refresh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            asset_catalog.initialize_library(library_root)
            ready_to_finalize = threading.Barrier(2)
            original_replace = asset_catalog.os.replace

            def replace_after_both_candidates_are_verified(
                source_path: object,
                destination_path: object,
            ) -> None:
                if Path(destination_path).parent == library_root / "originals":
                    ready_to_finalize.wait(timeout=5)
                original_replace(source_path, destination_path)

            with (
                patch.object(
                    asset_catalog.os,
                    "replace",
                    side_effect=replace_after_both_candidates_are_verified,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                futures = [
                    executor.submit(import_sources, library_root, [source])
                    for _ in range(2)
                ]
                results = [future.result() for future in futures]

            assets = list_assets(library_root)

        self.assertEqual(1, sum(result.new_assets for result in results))
        self.assertEqual(1, sum(result.duplicate_assets for result in results))
        self.assertEqual(1, sum(result.source_records_added for result in results))
        self.assertEqual(1, sum(result.source_records_refreshed for result in results))
        self.assertEqual(0, sum(result.failed_files for result in results))
        self.assertEqual(1, assets.assets[0]["source_record_count"])

    def test_reconciles_a_lost_race_winner_source_commit_acknowledgement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            library_root = root / "library"
            self._write_image(first_source, (255, 0, 0))
            second_source.write_bytes(first_source.read_bytes())
            asset_catalog.initialize_library(library_root)
            ready_to_finalize = threading.Barrier(2)
            original_replace = asset_catalog.os.replace
            original_recover = asset_catalog._recover_new_asset_commit

            def replace_after_both_candidates_are_verified(
                source_path: object,
                destination_path: object,
            ) -> None:
                if Path(destination_path).parent == library_root / "originals":
                    ready_to_finalize.wait(timeout=5)
                original_replace(source_path, destination_path)

            def recover_with_uncertain_winner_source_commit(
                *args: object,
                **kwargs: object,
            ) -> object:
                original_connect = asset_catalog.connect
                connection_count = 0

                def connect_with_one_uncertain_commit(
                    database_file: Path,
                ) -> sqlite3.Connection | _CommitOutcomeUncertainConnection:
                    nonlocal connection_count
                    connection_count += 1
                    connection = original_connect(database_file)
                    if connection_count == 1:
                        return _CommitOutcomeUncertainConnection(connection)
                    return connection

                with patch.object(
                    asset_catalog,
                    "connect",
                    side_effect=connect_with_one_uncertain_commit,
                ):
                    return original_recover(*args, **kwargs)

            with (
                patch.object(
                    asset_catalog.os,
                    "replace",
                    side_effect=replace_after_both_candidates_are_verified,
                ),
                patch.object(
                    asset_catalog,
                    "_recover_new_asset_commit",
                    side_effect=recover_with_uncertain_winner_source_commit,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                first_future = executor.submit(
                    import_sources,
                    library_root,
                    [first_source],
                )
                second_future = executor.submit(
                    import_sources,
                    library_root,
                    [second_source],
                )
                results = [first_future.result(), second_future.result()]

            assets = list_assets(library_root)

        self.assertEqual(1, sum(result.new_assets for result in results))
        self.assertEqual(1, sum(result.duplicate_assets for result in results))
        self.assertEqual(0, sum(result.failed_files for result in results))
        self.assertEqual(2, sum(result.source_records_added for result in results))
        self.assertEqual(2, assets.assets[0]["source_record_count"])

    def test_counts_a_duplicate_when_its_commit_acknowledgement_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            library_root = root / "library"
            self._write_image(first_source, (255, 0, 0))
            second_source.write_bytes(first_source.read_bytes())
            import_sources(library_root, [first_source])
            original_connect = asset_catalog.connect
            connection_count = 0

            def connect_with_uncertain_duplicate_commit(
                database_file: Path,
            ) -> sqlite3.Connection | _CommitOutcomeUncertainConnection:
                nonlocal connection_count
                connection_count += 1
                connection = original_connect(database_file)
                if connection_count == 2:
                    return _CommitOutcomeUncertainConnection(connection)
                return connection

            with patch.object(
                asset_catalog,
                "connect",
                side_effect=connect_with_uncertain_duplicate_commit,
            ):
                result = import_sources(library_root, [second_source])
            assets = list_assets(library_root)

        self.assertEqual(1, result.duplicate_assets)
        self.assertEqual(0, result.failed_files)
        self.assertEqual(1, result.source_records_added)
        self.assertEqual(2, assets.assets[0]["source_record_count"])

    def test_recovery_removes_only_recognized_uncataloged_import_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            asset_catalog.initialize_library(library_root)
            originals = library_root / "originals"
            abandoned_id = str(uuid.uuid4())
            abandoned_temporary = originals / f".{abandoned_id}.tmp"
            abandoned_final = originals / f"{abandoned_id}.png"
            abandoned_marker = originals / f".{abandoned_id}.png.pending"
            ordinary_content = originals / "keep-this-file.png"
            unrecognized_temporary = originals / ".not-an-import-id.tmp"
            abandoned_temporary.write_bytes(b"partial")
            abandoned_final.write_bytes(b"complete but uncataloged")
            abandoned_marker.touch()
            ordinary_content.write_bytes(b"ordinary content")
            unrecognized_temporary.write_bytes(b"unknown ownership")

            asset_catalog.initialize_library(library_root)
            recovered_names = {path.name for path in originals.iterdir()}
            result = import_sources(library_root, [source])
            remaining_names = {path.name for path in originals.iterdir()}

        self.assertEqual(1, result.new_assets)
        self.assertNotIn(abandoned_temporary.name, recovered_names)
        self.assertNotIn(abandoned_final.name, recovered_names)
        self.assertNotIn(abandoned_marker.name, recovered_names)
        self.assertNotIn(abandoned_temporary.name, remaining_names)
        self.assertNotIn(abandoned_final.name, remaining_names)
        self.assertNotIn(abandoned_marker.name, remaining_names)
        self.assertIn(ordinary_content.name, remaining_names)
        self.assertIn(unrecognized_temporary.name, remaining_names)

    def test_verifies_required_library_locations_before_copying_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reaction.png"
            library_root = root / "library"
            self._write_image(source, (255, 0, 0))
            original_open = asset_catalog.Path.open

            def open_with_unusable_originals(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> object:
                if (
                    path.parent == library_root / "originals"
                    and path.name.startswith(".library-location-check-")
                ):
                    raise PermissionError("originals is not writable")
                return original_open(path, *args, **kwargs)

            with (
                patch.object(
                    asset_catalog.Path,
                    "open",
                    new=open_with_unusable_originals,
                ),
                patch.object(
                    asset_catalog.shutil,
                    "copy2",
                    wraps=asset_catalog.shutil.copy2,
                ) as copy_file,
                self.assertRaisesRegex(PermissionError, "not writable"),
            ):
                import_sources(library_root, [source])

        copy_file.assert_not_called()

    def test_removes_overlapping_top_level_sources_before_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            nested_directory = source_directory / "nested"
            source_directory.mkdir()
            nested_directory.mkdir()
            nested_source = nested_directory / "reaction.png"
            self._write_image(nested_source, (255, 0, 0))

            result = import_sources(
                root / "library",
                [
                    source_directory,
                    nested_directory,
                    nested_source,
                    source_directory,
                ],
            )
            assets = list_assets(root / "library")

        self.assertEqual(4, result.selected_sources)
        self.assertEqual(1, result.effective_sources)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.processed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(0, result.duplicate_assets)
        self.assertEqual(1, len(assets.assets))

    def test_deduplicates_case_variant_directory_sources_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows path case rules are not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            source_directory.mkdir()
            self._write_image(source_directory / "reaction.png", (255, 0, 0))
            case_variant = Path(str(source_directory).upper())

            result = import_sources(
                root / "library",
                [source_directory, case_variant],
            )

        self.assertEqual(2, result.selected_sources)
        self.assertEqual(1, result.effective_sources)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.processed_files)

    def test_skips_reparse_entries_while_scanning_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            external_directory = root / "external"
            source_directory.mkdir()
            external_directory.mkdir()
            self._write_image(source_directory / "local.png", (255, 0, 0))
            self._write_image(external_directory / "external.png", (0, 0, 255))
            linked_directory = source_directory / "linked"
            try:
                linked_directory.symlink_to(external_directory, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks are unavailable: {error}")

            result = import_sources(root / "library", [source_directory])
            assets = list_assets(root / "library")

        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.supported_files)
        self.assertEqual(1, result.reparse_points_skipped)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, len(assets.assets))

    def test_counts_a_reparse_file_as_a_skip_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            source_directory.mkdir()
            reparse_source = source_directory / "unsafe.png"
            self._write_image(reparse_source, (255, 0, 0))
            original_lstat = asset_catalog.os.lstat

            def lstat_with_reparse(path: object, *args: object, **kwargs: object) -> object:
                metadata = original_lstat(path, *args, **kwargs)
                if Path(path) == reparse_source:
                    return _ReparseMetadata(metadata)
                return metadata

            with patch.object(
                asset_catalog.os,
                "lstat",
                side_effect=lstat_with_reparse,
            ):
                result = import_sources(root / "library", [source_directory])
                assets = list_assets(root / "library")

        self.assertEqual(0, result.discovered_files)
        self.assertEqual(1, result.reparse_points_skipped)
        self.assertEqual(0, result.new_assets)
        self.assertEqual(0, len(assets.assets))

    def test_rechecks_a_queued_directory_before_scanning_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            queued_directory = source_directory / "queued"
            source_directory.mkdir()
            queued_directory.mkdir()
            self._write_image(queued_directory / "reaction.png", (255, 0, 0))
            original_lstat = asset_catalog.os.lstat
            queued_directory_checks = 0

            def lstat_with_late_reparse(
                path: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                nonlocal queued_directory_checks
                metadata = original_lstat(path, *args, **kwargs)
                if Path(path) == queued_directory:
                    queued_directory_checks += 1
                    if queued_directory_checks == 2:
                        return _ReparseMetadata(metadata)
                return metadata

            with patch.object(
                asset_catalog.os,
                "lstat",
                side_effect=lstat_with_late_reparse,
            ):
                result = import_sources(root / "library", [source_directory])
                assets = list_assets(root / "library")

        self.assertEqual(2, queued_directory_checks)
        self.assertEqual(0, result.discovered_files)
        self.assertEqual(1, result.reparse_points_skipped)
        self.assertEqual(0, result.new_assets)
        self.assertEqual(0, len(assets.assets))

    def test_discards_entries_if_a_directory_becomes_a_reparse_point_while_opening(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            queued_directory = source_directory / "queued"
            source_directory.mkdir()
            queued_directory.mkdir()
            self._write_image(queued_directory / "reaction.png", (255, 0, 0))
            original_lstat = asset_catalog.os.lstat
            original_scandir = asset_catalog.os.scandir
            queued_directory_opened = False

            def scandir_while_reparse_replaces_directory(path: object) -> object:
                nonlocal queued_directory_opened
                if Path(path) == queued_directory:
                    queued_directory_opened = True
                return original_scandir(path)

            def lstat_after_opening_reparse(
                path: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                metadata = original_lstat(path, *args, **kwargs)
                if Path(path) == queued_directory and queued_directory_opened:
                    return _ReparseMetadata(metadata)
                return metadata

            with (
                patch.object(
                    asset_catalog.os,
                    "scandir",
                    side_effect=scandir_while_reparse_replaces_directory,
                ),
                patch.object(
                    asset_catalog.os,
                    "lstat",
                    side_effect=lstat_after_opening_reparse,
                ),
            ):
                result = import_sources(root / "library", [source_directory])
                assets = list_assets(root / "library")

        self.assertTrue(queued_directory_opened)
        self.assertEqual(0, result.discovered_files)
        self.assertEqual(1, result.reparse_points_skipped)
        self.assertEqual(0, result.new_assets)
        self.assertEqual(0, len(assets.assets))

    def test_locks_a_windows_directory_against_replacement_while_scanning(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows directory-sharing semantics are not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            moved_directory = root / "moved-reactions"
            source_directory.mkdir()
            (source_directory / "entry.txt").write_text("not media")
            replacement_was_blocked = False
            replacement_attempted = False

            def try_to_replace_source_directory() -> None:
                nonlocal replacement_attempted, replacement_was_blocked
                if replacement_attempted:
                    return
                replacement_attempted = True
                try:
                    source_directory.rename(moved_directory)
                except PermissionError:
                    replacement_was_blocked = True

            result = import_sources(
                root / "library",
                [source_directory],
                wait_for_permission=try_to_replace_source_directory,
            )
            source_directory.rename(moved_directory)
            source_was_released_after_scanning = moved_directory.exists()

        self.assertTrue(replacement_attempted)
        self.assertTrue(replacement_was_blocked)
        self.assertTrue(source_was_released_after_scanning)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.unsupported_files)

    def test_locks_windows_descendants_against_replacement_while_scanning(
        self,
    ) -> None:
        if os.name != "nt":
            self.skipTest("Windows directory-sharing semantics are not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            nested_directory = source_directory / "nested"
            moved_directory = source_directory / "moved-nested"
            source_directory.mkdir()
            nested_directory.mkdir()
            (nested_directory / "entry.txt").write_text("not media")
            replacement_was_blocked = False
            replacement_attempted = False

            def try_to_replace_nested_directory() -> None:
                nonlocal replacement_attempted, replacement_was_blocked
                if replacement_attempted:
                    return
                replacement_attempted = True
                try:
                    nested_directory.rename(moved_directory)
                except PermissionError:
                    replacement_was_blocked = True

            result = import_sources(
                root / "library",
                [source_directory],
                wait_for_permission=try_to_replace_nested_directory,
            )
            nested_directory.rename(moved_directory)
            descendant_was_released_after_scanning = moved_directory.exists()

        self.assertTrue(replacement_attempted)
        self.assertTrue(replacement_was_blocked)
        self.assertTrue(descendant_was_released_after_scanning)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.unsupported_files)

    def test_records_unreadable_directory_as_a_scan_failure_and_imports_other_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unreadable_directory = root / "unreadable"
            unreadable_directory.mkdir()
            usable_source = root / "usable.png"
            self._write_image(usable_source, (255, 0, 0))
            original_scandir = asset_catalog.os.scandir

            def scandir_with_unreadable_directory(path: object) -> object:
                if Path(path) == unreadable_directory:
                    raise PermissionError("Access denied")
                return original_scandir(path)

            with patch.object(
                asset_catalog.os,
                "scandir",
                side_effect=scandir_with_unreadable_directory,
            ):
                result = import_sources(
                    root / "library",
                    [unreadable_directory, usable_source],
                )
            assets = list_assets(root / "library")

        self.assertEqual(1, result.scan_failures)
        self.assertEqual(1, len(result.failure_details))
        self.assertEqual(ImportFailureStage.SCAN, result.failure_details[0].stage)
        self.assertEqual(ImportFailureCode.SCAN_FAILED, result.failure_details[0].code)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.processed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, len(assets.assets))

    def test_reports_directory_entry_scan_failures_in_stable_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            source_directory.mkdir()
            first_unreadable_source = source_directory / "a-unreadable.png"
            second_unreadable_source = source_directory / "z-unreadable.png"
            self._write_image(first_unreadable_source, (255, 0, 0))
            self._write_image(second_unreadable_source, (0, 0, 255))
            unreadable_sources = {
                first_unreadable_source,
                second_unreadable_source,
            }
            original_lstat = asset_catalog.os.lstat

            def lstat_with_unreadable_entries(
                path: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                if Path(path) in unreadable_sources:
                    raise PermissionError("Access denied")
                return original_lstat(path, *args, **kwargs)

            with patch.object(
                asset_catalog.os,
                "lstat",
                side_effect=lstat_with_unreadable_entries,
            ):
                result = import_sources(root / "library", [source_directory])

        self.assertEqual(2, result.scan_failures)
        self.assertEqual(
            ["a-unreadable.png", "z-unreadable.png"],
            [failure.source_name for failure in result.failure_details],
        )

    def test_stops_at_the_unique_discovery_limit_before_creating_the_library(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            library_root = root / "library"
            source_directory.mkdir()
            for name in ("one.txt", "two.txt", "three.txt"):
                (source_directory / name).write_text(name)

            self.assertEqual(100_000, asset_catalog.MAX_IMPORT_DISCOVERED_FILES)
            with patch.object(asset_catalog, "MAX_IMPORT_DISCOVERED_FILES", 2):
                with self.assertRaises(ImportBatchError) as raised:
                    import_sources(library_root, [source_directory])
            library_created = library_root.exists()

        error = raised.exception
        self.assertEqual(ImportBatchErrorCode.FILE_LIMIT_EXCEEDED, error.code)
        self.assertIsNotNone(error.partial_result)
        assert error.partial_result is not None
        self.assertEqual(2, error.partial_result.discovered_files)
        self.assertEqual(2, error.partial_result.unsupported_files)
        self.assertEqual(1, error.partial_result.scan_failures)
        self.assertFalse(library_created)

    def test_checks_pause_permission_between_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "reactions"
            source_directory.mkdir()
            (source_directory / "first").mkdir()
            (source_directory / "second").mkdir()
            pause_checks: list[None] = []

            result = import_sources(
                root / "library",
                [source_directory],
                wait_for_permission=lambda: pause_checks.append(None),
            )

        self.assertEqual(0, result.discovered_files)
        self.assertEqual(2, len(pause_checks))

    def test_empty_directories_and_unsupported_files_complete_as_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            empty_directory = root / "empty"
            mixed_directory = root / "mixed"
            empty_directory.mkdir()
            mixed_directory.mkdir()
            (mixed_directory / "notes.txt").write_text("not media")

            result = import_sources(
                root / "library",
                [empty_directory, mixed_directory],
            )
            assets = list_assets(root / "library")

        self.assertEqual(2, result.selected_sources)
        self.assertEqual(2, result.effective_sources)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(0, result.supported_files)
        self.assertEqual(1, result.unsupported_files)
        self.assertEqual(0, result.processed_files)
        self.assertEqual(0, result.failure_count)
        self.assertEqual(0, len(assets.assets))

    def test_rejects_the_whole_batch_before_library_creation_when_a_source_is_invalid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            valid_source = root / "valid.png"
            self._write_image(valid_source, (255, 0, 0))

            with self.assertRaises(ImportBatchPreflightError) as raised:
                import_sources(
                    library_root,
                    [valid_source, root / "missing.png"],
                )

            self.assertIsNone(raised.exception.partial_result)
            self.assertFalse(library_root.exists())

    def test_processes_repeated_spellings_of_one_canonical_path_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "same.png"
            (root / "alternate").mkdir()
            self._write_image(source, (255, 0, 0))

            result = import_sources(
                root / "library",
                [source, root / "alternate" / ".." / source.name],
            )
            assets = list_assets(root / "library")

        self.assertEqual(2, result.selected_sources)
        self.assertEqual(1, result.effective_sources)
        self.assertEqual(1, result.discovered_files)
        self.assertEqual(1, result.processed_files)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(0, result.duplicate_assets)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(1, assets.assets[0]["source_record_count"])

    def test_rejects_a_source_inside_the_library_before_initializing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            library_root.mkdir()
            source = library_root / "source.png"
            self._write_image(source, (255, 0, 0))

            with self.assertRaises(ImportBatchPreflightError):
                import_sources(library_root, [source])

            self.assertEqual([source], list(library_root.iterdir()))

    def test_rejects_more_than_256_top_level_sources_before_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source = root / "source.png"
            self._write_image(source, (255, 0, 0))

            with self.assertRaises(ImportBatchPreflightError):
                import_sources(library_root, [source] * 257)

            self.assertFalse(library_root.exists())

    def test_rejects_a_non_path_source_with_a_preflight_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"

            with self.assertRaises(ImportBatchPreflightError):
                import_sources(library_root, [42])  # type: ignore[list-item]

            self.assertFalse(library_root.exists())

    def test_rejects_a_malformed_sources_container_before_library_creation(
        self,
    ) -> None:
        malformed_containers = (
            "source.png",
            {"source.png"},
            {"source": "source.png"},
            None,
        )

        for malformed in malformed_containers:
            with self.subTest(container_type=type(malformed).__name__):
                with tempfile.TemporaryDirectory() as temp_dir:
                    library_root = Path(temp_dir) / "library"

                    with self.assertRaises(ImportBatchPreflightError) as raised:
                        import_sources(library_root, malformed)  # type: ignore[arg-type]

                    self.assertIn("sequence", raised.exception.detail.lower())
                    self.assertFalse(library_root.exists())

    def test_accepts_an_empty_directory_as_an_import_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "source"
            source_directory.mkdir()

            result = import_sources(root / "library", [source_directory])

        self.assertEqual(1, result.selected_sources)
        self.assertEqual(1, result.effective_sources)
        self.assertEqual(0, result.discovered_files)
        self.assertEqual(0, result.failure_count)

    def test_rejects_paths_that_cannot_be_safely_transported(self) -> None:
        unsafe_paths = (
            "bad\x00path.png",
            "bad\rpath.png",
            "bad\npath.png",
            "\ud800.png",
            f"{'a' * (32 * 1024)}.png",
        )

        for unsafe_path in unsafe_paths:
            with self.subTest(unsafe_path=repr(unsafe_path)):
                with tempfile.TemporaryDirectory() as temp_dir:
                    library_root = Path(temp_dir) / "library"

                    with self.assertRaises(ImportBatchPreflightError) as raised:
                        import_sources(library_root, [unsafe_path])

                    self.assertIn("transport-safe", raised.exception.detail)
                    self.assertFalse(library_root.exists())

    def test_rejects_a_top_level_reparse_file_before_resolving_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.png"
            reparse_source = root / "linked.png"
            self._write_image(target, (255, 0, 0))
            try:
                reparse_source.symlink_to(target)
            except OSError as error:
                self.skipTest(f"File symlinks are unavailable: {error}")

            with self.assertRaises(ImportBatchPreflightError) as raised:
                import_sources(root / "library", [reparse_source])

            self.assertIn("reparse", raised.exception.detail.lower())
            self.assertFalse((root / "library").exists())

    def test_keeps_distinct_hard_link_paths_as_source_records_on_one_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            self._write_image(first_source, (255, 0, 0))
            second_source.hardlink_to(first_source)

            result = import_sources(
                root / "library",
                [first_source, second_source],
            )
            assets = list_assets(root / "library")

        self.assertEqual(2, result.effective_sources)
        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, result.duplicate_assets)
        self.assertEqual(2, result.source_records_added)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(2, assets.assets[0]["source_record_count"])
        self.assertEqual(
            {str(first_source), str(second_source)},
            {
                record["source_path"]
                for record in assets.assets[0]["source_records"]
            },
        )

    def test_equal_content_at_independent_paths_creates_one_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.png"
            second_source = root / "second.png"
            self._write_image(first_source, (255, 0, 0))
            second_source.write_bytes(first_source.read_bytes())

            result = import_sources(
                root / "library",
                [first_source, second_source],
            )
            assets = list_assets(root / "library")

        self.assertEqual(1, result.new_assets)
        self.assertEqual(1, result.duplicate_assets)
        self.assertEqual(2, result.source_records_added)
        self.assertEqual(1, len(assets.assets))
        self.assertEqual(2, assets.assets[0]["source_record_count"])

    def test_preserves_source_record_history_when_a_source_path_changes_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            library_root = root / "library"
            source = root / "changing.png"
            self._write_image(source, (255, 0, 0))
            first_result = import_sources(library_root, [source])

            self._write_image(source, (0, 0, 255))
            second_result = import_sources(library_root, [source])
            assets = list_assets(library_root)

        self.assertEqual(1, first_result.new_assets)
        self.assertEqual(1, second_result.new_assets)
        self.assertEqual(2, len(assets.assets))
        self.assertTrue(
            all(
                [
                    record["source_path"]
                    for record in asset["source_records"]
                ]
                == [str(source)]
                for asset in assets.assets
            )
        )


if __name__ == "__main__":
    unittest.main()
