from __future__ import annotations

import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import patch

from memesort_worker import asset_catalog
from memesort_worker.library import (
    ImportBatchError,
    ImportBatchErrorCode,
    ImportBatchPreflightError,
    ImportBatchResult,
    ImportFailure,
    ImportFailureCode,
    ImportFailureStage,
    ImportFolderResult,
    import_folder,
)


def make_import_batch_result(**overrides: Any) -> ImportBatchResult:
    values = {
        "library_root": "C:/MemeSort",
        "selected_sources": 1,
        "effective_sources": 1,
        "discovered_files": 0,
        "supported_files": 0,
        "unsupported_files": 0,
        "reparse_points_skipped": 0,
        "scan_failures": 0,
        "processed_files": 0,
        "succeeded_files": 0,
        "failed_files": 0,
        "new_assets": 0,
        "duplicate_assets": 0,
        "source_records_added": 0,
        "source_records_refreshed": 0,
        "jobs_created": 0,
        "active_recipe_id": "recipe-1",
    }
    values.update(overrides)
    return ImportBatchResult(**values)


class ImportFailureContractTests(unittest.TestCase):
    def test_failure_stages_have_stable_codes(self) -> None:
        cases = (
            (ImportFailureStage.SCAN, ImportFailureCode.SCAN_FAILED),
            (ImportFailureStage.READ, ImportFailureCode.SOURCE_READ_FAILED),
            (ImportFailureStage.COPY, ImportFailureCode.LIBRARY_COPY_FAILED),
            (ImportFailureStage.VALIDATION, ImportFailureCode.IMAGE_DECODE_FAILED),
            (ImportFailureStage.DATABASE, ImportFailureCode.DATABASE_WRITE_FAILED),
        )

        for stage, code in cases:
            with self.subTest(stage=stage):
                failure = ImportFailure(
                    stage=stage,
                    code=code,
                    source_name="reaction.png",
                    detail="The source could not be imported.",
                )
                self.assertEqual(stage.value, failure.to_dict()["stage"])
                self.assertEqual(code.value, failure.to_dict()["code"])

    def test_failure_exposes_stable_stage_code_and_display_safe_text(self) -> None:
        failure = ImportFailure(
            stage=ImportFailureStage.READ,
            code=ImportFailureCode.SOURCE_READ_FAILED,
            source_name="C:\\private\\reactions\\bad.png\n",
            detail="Could not read source.\r\nTry selecting it again.",
        )

        self.assertEqual(
            {
                "stage": "read",
                "code": "source_read_failed",
                "source_name": "bad.png",
                "detail": "Could not read source. Try selecting it again.",
            },
            failure.to_dict(),
        )

    def test_failure_code_must_belong_to_its_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not belong to stage"):
            ImportFailure(
                stage=ImportFailureStage.SCAN,
                code=ImportFailureCode.DATABASE_WRITE_FAILED,
                source_name="memes",
                detail="Could not enumerate this source.",
            )


class ImportBatchResultContractTests(unittest.TestCase):
    def test_result_exposes_counts_and_keeps_unsupported_skips_out_of_failures(self) -> None:
        result = make_import_batch_result(
            selected_sources=2,
            discovered_files=3,
            supported_files=2,
            unsupported_files=1,
            processed_files=2,
            succeeded_files=2,
            new_assets=1,
            duplicate_assets=1,
            source_records_added=2,
            jobs_created=3,
        )

        self.assertEqual(0, result.failure_count)
        self.assertFalse(result.failures_truncated)
        self.assertEqual([], result.to_dict()["failure_details"])
        self.assertEqual(1, result.to_dict()["unsupported_files"])

    def test_result_caps_failure_details_and_preserves_full_failure_count(self) -> None:
        failures = tuple(
            ImportFailure(
                stage=ImportFailureStage.READ,
                code=ImportFailureCode.SOURCE_READ_FAILED,
                source_name=f"bad-{index}.png",
                detail="Could not read source.",
            )
            for index in range(101)
        )
        result = make_import_batch_result(
            discovered_files=101,
            supported_files=101,
            processed_files=101,
            failed_files=101,
            failure_details=failures,
        )

        self.assertEqual(101, result.failure_count)
        self.assertEqual(100, len(result.failure_details))
        self.assertTrue(result.failures_truncated)
        self.assertEqual(100, len(result.to_dict()["failure_details"]))

    def test_result_rejects_broken_arithmetic(self) -> None:
        valid = {
            "discovered_files": 2,
            "supported_files": 2,
            "processed_files": 2,
            "succeeded_files": 2,
            "new_assets": 2,
            "source_records_added": 2,
            "jobs_created": 6,
        }
        invalid_changes = (
            {"unsupported_files": 1},
            {"processed_files": 1},
            {"succeeded_files": 1, "failed_files": 1},
            {"new_assets": 1},
            {"source_records_added": 1},
            {"effective_sources": 2},
            {"jobs_created": -1},
        )

        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                make_import_batch_result(**(valid | changes))


class ImportBatchErrorContractTests(unittest.TestCase):
    def test_fatal_error_can_carry_a_partial_result(self) -> None:
        partial_result = make_import_batch_result(active_recipe_id=None)
        error = ImportBatchError(
            code=ImportBatchErrorCode.FILE_LIMIT_EXCEEDED,
            detail="Import stopped after the discovery limit was reached.",
            partial_result=partial_result,
        )

        self.assertIs(partial_result, error.partial_result)
        self.assertEqual(
            partial_result.to_dict(),
            error.to_dict()["partial_result"],
        )

    def test_preflight_error_never_carries_a_partial_result(self) -> None:
        error = ImportBatchPreflightError(
            code=ImportBatchErrorCode.INVALID_SOURCE,
            detail="One selected source is not a regular file or directory.",
        )

        self.assertIsNone(error.partial_result)
        self.assertIsNone(error.to_dict()["partial_result"])


class ImportFolderCompatibilityTests(unittest.TestCase):
    def test_folder_entry_point_keeps_its_established_result_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            source_root.mkdir()

            result = import_folder(root / "library", source_root)

        self.assertIsInstance(result, ImportFolderResult)
        self.assertEqual(str(source_root.resolve()), result.source_folder)
        self.assertEqual(
            {
                "library_root",
                "source_folder",
                "discovered_files",
                "supported_files",
                "unsupported_files",
                "new_assets",
                "duplicate_assets",
                "source_records_added",
                "source_records_refreshed",
                "jobs_created",
                "active_recipe_id",
            },
            {field.name for field in fields(result)},
        )

    def test_folder_adapter_stops_at_the_scan_limit_before_library_creation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "source"
            library_root = root / "library"
            source_root.mkdir()
            for name in ("one.txt", "two.txt", "three.txt"):
                (source_root / name).write_text(name)

            with patch.object(asset_catalog, "MAX_IMPORT_DISCOVERED_FILES", 2):
                with self.assertRaises(ImportBatchError) as raised:
                    import_folder(library_root, source_root)
            library_created = library_root.exists()

        self.assertEqual(ImportBatchErrorCode.FILE_LIMIT_EXCEEDED, raised.exception.code)
        self.assertIsNotNone(raised.exception.partial_result)
        self.assertFalse(library_created)


if __name__ == "__main__":
    unittest.main()
