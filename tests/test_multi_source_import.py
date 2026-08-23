from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from memesort_worker.library import (
    ImportBatchPreflightError,
    import_sources,
    list_assets,
)


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

    def test_rejects_a_directory_when_only_explicit_files_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_directory = root / "source"
            source_directory.mkdir()

            with self.assertRaises(ImportBatchPreflightError) as raised:
                import_sources(root / "library", [source_directory])

            self.assertIn("regular file", raised.exception.detail)
            self.assertFalse((root / "library").exists())

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
