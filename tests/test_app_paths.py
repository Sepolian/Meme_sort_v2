from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memesort_worker.app_paths import (
    ENV_APP_ROOT,
    ENV_LIBRARY_ROOT,
    ENV_MUTABLE_ROOT,
    AppPaths,
)


class AppPathsDiscoveryTests(unittest.TestCase):
    def test_development_layout_points_at_source_checkout(self) -> None:
        paths = AppPaths.discover(env={})

        package_dir = Path(__file__).resolve().parents[1] / "memesort_worker"
        self.assertEqual(paths.application_root, package_dir.parent)
        self.assertEqual(paths.static_root, package_dir / "web_static")
        self.assertEqual(paths.manifest_path, package_dir.parent / "runtime-manifest.json")
        self.assertTrue(paths.static_root.is_dir())
        self.assertTrue(paths.manifest_path.is_file())

    def test_localappdata_drives_the_mutable_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "Local"
            paths = AppPaths.discover(env={"LOCALAPPDATA": str(local)})

            self.assertEqual(paths.mutable_root, (local / "MemeSort").resolve())
            self.assertEqual(paths.runtime_root, (local / "MemeSort" / "runtime").resolve())
            self.assertEqual(paths.models_root, (local / "MemeSort" / "models").resolve())
            self.assertEqual(paths.logs_root, (local / "MemeSort" / "logs").resolve())

    def test_roaming_location_is_the_default_library_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            roaming = Path(temp_dir) / "Roaming"
            paths = AppPaths.discover(env={"APPDATA": str(roaming)})

            self.assertEqual(
                paths.default_library_root,
                (roaming / "MemeSort").resolve(),
            )

    def test_environment_overrides_take_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_root = root / "install"
            (app_root / "memesort_worker" / "web_static").mkdir(parents=True)
            mutable = root / "data"
            library = root / "library"

            paths = AppPaths.discover(
                env={
                    ENV_APP_ROOT: str(app_root),
                    ENV_MUTABLE_ROOT: str(mutable),
                    ENV_LIBRARY_ROOT: str(library),
                }
            )

            self.assertEqual(paths.application_root, app_root.resolve())
            self.assertEqual(
                paths.static_root,
                (app_root / "memesort_worker" / "web_static").resolve(),
            )
            self.assertEqual(paths.manifest_path, (app_root / "runtime-manifest.json").resolve())
            self.assertEqual(paths.mutable_root, mutable.resolve())
            self.assertEqual(paths.default_library_root, library.resolve())

    def test_ensure_mutable_tree_creates_writable_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mutable = Path(temp_dir) / "nested" / "MemeSortData"
            paths = AppPaths.discover(env={ENV_MUTABLE_ROOT: str(mutable)})

            self.assertFalse(paths.mutable_root.exists())
            paths.ensure_mutable_tree()

            self.assertTrue(paths.mutable_root.is_dir())
            self.assertTrue(paths.runtime_root.is_dir())
            self.assertTrue(paths.models_root.is_dir())
            self.assertTrue(paths.logs_root.is_dir())

    def test_frozen_layout_matches_the_bundled_data_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_root = Path(temp_dir) / "_internal"
            (bundle_root / "memesort_worker" / "web_static").mkdir(parents=True)
            (bundle_root / "runtime-manifest.json").write_text("{}", encoding="utf-8")
            exe_dir = Path(temp_dir)

            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                sys, "_MEIPASS", str(bundle_root), create=True
            ), mock.patch.object(sys, "executable", str(exe_dir / "MemeSort.exe")):
                paths = AppPaths.discover(env={})

            self.assertEqual(paths.application_root, exe_dir.resolve())
            self.assertEqual(
                paths.static_root,
                bundle_root / "memesort_worker" / "web_static",
            )
            self.assertEqual(paths.manifest_path, bundle_root / "runtime-manifest.json")


if __name__ == "__main__":
    unittest.main()
