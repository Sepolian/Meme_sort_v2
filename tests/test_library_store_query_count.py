"""Query-count regression tests for LibraryStore read projections.

The number of SQL statements a projection issues must be bounded by the
projection type, not grow with the number of Assets in the Library.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Callable

from PIL import Image

from memesort_worker.library import import_folder
from memesort_worker.library_store import LibraryStore


def _build_library(root: Path, asset_count: int) -> Path:
    library_root = root / "library"
    source_root = root / "source"
    source_root.mkdir()
    for index in range(asset_count):
        color = (index % 256, (index * 7) % 256, (index * 13) % 256)
        Image.new("RGB", (8, 8), color).save(source_root / f"asset-{index:03d}.png", format="PNG")
    import_folder(library_root, source_root)
    return library_root


def _count_statements(store: LibraryStore, operation: Callable[[], object]) -> int:
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        operation()
    finally:
        store._conn.set_trace_callback(None)
    return len(statements)


class LibraryStoreQueryCountTests(unittest.TestCase):
    def _measure(self, asset_count: int) -> dict[str, int]:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = _build_library(Path(temp_dir), asset_count)
            with LibraryStore(library_root) as store:
                return {
                    "summaries": _count_statements(store, store.list_asset_summaries),
                    "detailed": _count_statements(store, store.list_assets_detailed),
                    "status": _count_statements(store, store.get_library_status),
                    "snapshot": _count_statements(store, store.read_library_snapshot),
                }

    def test_projection_query_count_does_not_grow_with_asset_count(self) -> None:
        baseline = self._measure(1)
        many = self._measure(100)
        for projection, baseline_count in baseline.items():
            with self.subTest(projection=projection):
                self.assertEqual(
                    baseline_count,
                    many[projection],
                    f"{projection} issued {many[projection]} statements for 100 assets "
                    f"but {baseline_count} for one asset",
                )


if __name__ == "__main__":
    unittest.main()
