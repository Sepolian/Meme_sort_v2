from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from memesort_worker import asset_catalog
from memesort_worker.indexing_pipeline import run_pending_jobs
from memesort_worker.library import (
    accept_duplicate_pair,
    clear_accepted_pairs,
    delete_asset,
    import_folder,
    list_assets,
)
from memesort_worker.library_store import LibraryStore
from memesort_worker.webapp import create_app
from runtime_fakes import FakeIndexingRuntime


class AcceptedDuplicatePairTests(unittest.TestCase):
    def _write_image(self, path: Path, color: tuple[int, int, int]) -> None:
        Image.new("RGB", (40, 30), color).save(path, format="PNG")

    def _import_two_images(self, root: Path) -> Path:
        library_root = root / "library"
        source_root = root / "source"
        source_root.mkdir(parents=True, exist_ok=True)
        self._write_image(source_root / "first.png", (255, 0, 0))
        self._write_image(source_root / "second.png", (0, 255, 0))
        import_folder(library_root, source_root)
        return library_root

    def _index_all(self, library_root: Path) -> None:
        result = run_pending_jobs(library_root, FakeIndexingRuntime())
        self.assertEqual(0, result.failed_jobs)

    def _asset_ids(self, library_root: Path) -> list[str]:
        return [str(asset["asset_id"]) for asset in list_assets(library_root).assets]

    def _request(self, app, method: str, path: str, payload=None, query: str = ""):
        body = json.dumps(payload or {}).encode("utf-8")
        captured: dict[str, str] = {}

        def start_response(status: str, _headers: object) -> None:
            captured["status"] = status

        response_body = b"".join(
            app(
                {
                    "REQUEST_METHOD": method,
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "CONTENT_LENGTH": str(len(body)),
                    "wsgi.input": BytesIO(body),
                },
                start_response,
            )
        )
        return captured["status"], json.loads(response_body.decode("utf-8"))

    def _pair_rows(self, library_root: Path) -> list[tuple[str, str]]:
        conn = asset_catalog.connect(
            asset_catalog.database_path(Path(library_root).resolve())
        )
        try:
            return [
                (str(row["asset_a_id"]), str(row["asset_b_id"]))
                for row in conn.execute(
                    "SELECT asset_a_id, asset_b_id FROM accepted_duplicate_pair"
                ).fetchall()
            ]
        finally:
            conn.close()

    def test_accept_is_canonical_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            first_id, second_id = self._asset_ids(library_root)

            first = accept_duplicate_pair(library_root, second_id, first_id)
            self.assertEqual(
                tuple(sorted([first_id, second_id])),
                (first.asset_a_id, first.asset_b_id),
            )
            self.assertFalse(first.already_accepted)

            second = accept_duplicate_pair(library_root, first_id, second_id)
            self.assertTrue(second.already_accepted)
            self.assertEqual(
                (first.asset_a_id, first.asset_b_id),
                (second.asset_a_id, second.asset_b_id),
            )
            self.assertEqual(1, len(self._pair_rows(library_root)))

    def test_excludes_both_orderings_from_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            self._index_all(library_root)
            first_id, second_id = self._asset_ids(library_root)

            with LibraryStore(library_root) as store:
                before = store.scan_duplicate_assets(threshold=0.9)
            self.assertEqual(1, len(before.pairs))

            accept_duplicate_pair(library_root, first_id, second_id)

            with LibraryStore(library_root) as store:
                after = store.scan_duplicate_assets(threshold=0.9)
            self.assertEqual([], after.pairs)

    def test_clear_restores_pairs_without_deleting_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            self._index_all(library_root)
            first_id, second_id = self._asset_ids(library_root)

            accept_duplicate_pair(library_root, first_id, second_id)
            cleared = clear_accepted_pairs(library_root)
            self.assertEqual(1, cleared.cleared_pairs)
            self.assertEqual([], self._pair_rows(library_root))

            with LibraryStore(library_root) as store:
                restored = store.scan_duplicate_assets(threshold=0.9)
            self.assertEqual(1, len(restored.pairs))
            self.assertEqual(2, len(self._asset_ids(library_root)))

            cleared_again = clear_accepted_pairs(library_root)
            self.assertEqual(0, cleared_again.cleared_pairs)

    def test_validation_rejects_equal_unknown_and_malformed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            first_id, _ = self._asset_ids(library_root)

            with self.assertRaisesRegex(ValueError, "same asset"):
                accept_duplicate_pair(library_root, first_id, first_id)
            with self.assertRaisesRegex(ValueError, "Unknown asset"):
                accept_duplicate_pair(
                    library_root, first_id, "00000000-0000-0000-0000-000000000000"
                )
            for malformed in ("", "   ", None, 123):
                with self.subTest(malformed=malformed), self.assertRaisesRegex(
                    ValueError, "Invalid asset id"
                ):
                    accept_duplicate_pair(library_root, malformed, first_id)

    def test_api_error_shape_for_validation_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            first_id, second_id = self._asset_ids(library_root)
            app = create_app(str(library_root))
            try:
                status, payload = self._request(
                    app,
                    "POST",
                    "/api/accept-duplicate-pair",
                    {"asset_a_id": first_id, "asset_b_id": first_id},
                )
                self.assertTrue(status.startswith("400 "))
                self.assertEqual("ValueError", payload["error"])

                status, payload = self._request(
                    app,
                    "POST",
                    "/api/accept-duplicate-pair",
                    {"asset_a_id": first_id, "asset_b_id": "missing-id"},
                )
                self.assertTrue(status.startswith("400 "))
                self.assertEqual("ValueError", payload["error"])

                status, payload = self._request(
                    app, "POST", "/api/accept-duplicate-pair", {}
                )
                self.assertTrue(status.startswith("400 "))
                self.assertEqual("ValueError", payload["error"])

                status, payload = self._request(
                    app,
                    "POST",
                    "/api/accept-duplicate-pair",
                    {"asset_a_id": second_id, "asset_b_id": first_id},
                )
                self.assertTrue(status.startswith("200 "))
                self.assertIn("already_accepted", payload)

                status, payload = self._request(
                    app, "POST", "/api/clear-accepted-pairs", {}
                )
                self.assertTrue(status.startswith("200 "))
                self.assertEqual(1, payload["cleared_pairs"])
            finally:
                app.shutdown()

    def test_delete_cascades_to_acceptance_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            first_id, second_id = self._asset_ids(library_root)

            accept_duplicate_pair(library_root, first_id, second_id)
            self.assertEqual(1, len(self._pair_rows(library_root)))

            delete_asset(library_root, first_id)
            self.assertEqual([], self._pair_rows(library_root))
            self.assertEqual([second_id], self._asset_ids(library_root))

    def test_schema_migrates_existing_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = self._import_two_images(Path(temp_dir))
            db_path = asset_catalog.database_path(Path(library_root).resolve())
            conn = asset_catalog.connect(db_path)
            try:
                conn.execute("DROP TABLE accepted_duplicate_pair")
                conn.commit()
            finally:
                conn.close()

            asset_catalog.initialize_library(library_root)
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'accepted_duplicate_pair'"
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertIn("ON DELETE CASCADE", row[0])
            self.assertIn("CHECK (asset_a_id < asset_b_id)", row[0])

            first_id, second_id = self._asset_ids(library_root)
            result = accept_duplicate_pair(library_root, first_id, second_id)
            self.assertFalse(result.already_accepted)


if __name__ == "__main__":
    unittest.main()
