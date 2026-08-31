from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memesort_worker.app_paths import (
    ENV_APP_ROOT,
    ENV_LIBRARY_ROOT,
    ENV_PORTABLE_ROOT,
    AppPaths,
)


class AppPathsDiscoveryTests(unittest.TestCase):
    def test_portable_layout_keeps_all_mutable_data_beside_the_executable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portable_root = Path(temp_dir) / "MemeSort-portable"
            paths = AppPaths.discover(
                env={
                    ENV_PORTABLE_ROOT: str(portable_root),
                    "APPDATA": r"C:\\Users\\Someone\\AppData\\Roaming",
                }
            )

            data_root = portable_root.resolve() / "MemeSortData"
            self.assertEqual(paths.portable_root, portable_root.resolve())
            self.assertEqual(paths.data_root, data_root)
            self.assertEqual(paths.default_library_root, data_root / "library")
            self.assertEqual(paths.models_root, data_root / "models")
            self.assertEqual(paths.runtime_root, data_root / "runtime")

    def test_development_layout_points_at_source_checkout(self) -> None:
        paths = AppPaths.discover(env={})

        package_dir = Path(__file__).resolve().parents[1] / "memesort_worker"
        self.assertEqual(paths.application_root, package_dir.parent)
        self.assertEqual(paths.static_root, package_dir / "web_static")
        self.assertEqual(paths.manifest_path, package_dir.parent / "runtime-manifest.json")
        self.assertTrue(paths.static_root.is_dir())
        self.assertTrue(paths.manifest_path.is_file())

    def test_application_root_is_the_default_portable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir) / "MemeSort-portable"
            paths = AppPaths.discover(
                env={
                    ENV_APP_ROOT: str(app_root),
                    "APPDATA": str(Path(temp_dir) / "Roaming"),
                }
            )

            self.assertEqual(paths.portable_root, app_root.resolve())
            self.assertEqual(paths.default_library_root, app_root.resolve() / "MemeSortData" / "library")

    def test_environment_overrides_take_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_root = root / "install"
            (app_root / "memesort_worker" / "web_static").mkdir(parents=True)
            library = root / "library"

            paths = AppPaths.discover(
                env={
                    ENV_APP_ROOT: str(app_root),
                    ENV_LIBRARY_ROOT: str(library),
                }
            )

            self.assertEqual(paths.application_root, app_root.resolve())
            self.assertEqual(
                paths.static_root,
                (app_root / "memesort_worker" / "web_static").resolve(),
            )
            self.assertEqual(paths.manifest_path, (app_root / "runtime-manifest.json").resolve())
            self.assertEqual(paths.default_library_root, library.resolve())

    def test_frozen_layout_matches_the_bundled_data_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_root = (Path(temp_dir) / "_internal").resolve()
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
