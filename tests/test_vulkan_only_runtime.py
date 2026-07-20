from __future__ import annotations

import json
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from memesort_worker.app_commands import search_text
from memesort_worker.app_state import build_app_state
from memesort_worker.asset_preprocessing import preprocess_image_bytes
from memesort_worker.embedding_backend import get_embedding_backend
from memesort_worker.library import (
    initialize_library,
    import_folder,
    list_assets,
)
from memesort_worker import library as library_module
from memesort_worker.recipe_provider import default_provider
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.runtime_manifest import load_runtime_manifest


class VulkanOnlyRuntimeTests(unittest.TestCase):
    def test_runtime_descriptor_is_derived_from_the_manifest(self) -> None:
        manifest = load_runtime_manifest()
        runtime = get_runtime_descriptor()

        self.assertEqual("llama.cpp", runtime.backend_name)
        self.assertEqual(manifest.platform.device, runtime.device)
        self.assertEqual(manifest.llama_cpp.build, runtime.llama_cpp_build)
        self.assertEqual(manifest.model.id, runtime.model_id)
        self.assertEqual(manifest.model.output_dimension, runtime.output_dimension)
        self.assertEqual(manifest.embedding.storage_dtype, runtime.storage_dtype)
        self.assertEqual(manifest.runtime_fingerprint, runtime.runtime_fingerprint)
        self.assertEqual(manifest.recipe_fingerprint, runtime.recipe_fingerprint)
        self.assertEqual(manifest.preprocessing.version, runtime.preprocessing_version)

    def test_new_library_uses_manifest_recipe_and_read_only_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            initialize_library(root)
            assets = list_assets(root)
            state = build_app_state(root)

        self.assertEqual(get_runtime_descriptor().to_dict(), state.runtime)
        self.assertIn(" / vulkan", assets.active_recipe_label)

    def test_runtime_has_no_selection_api_or_persisted_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            initialize_library(root)
            conn = sqlite3.connect(root / "library.sqlite")
            try:
                persisted = conn.execute(
                    "SELECT 1 FROM worker_state WHERE key = 'runtime_settings'"
                ).fetchone()
            finally:
                conn.close()

        self.assertIsNone(persisted)
        for name in (
            "RuntimeProfileSpec",
            "ModelVariantSpec",
            "RuntimeSettings",
            "list_runtime_profiles",
            "list_model_variants",
            "get_runtime_profile",
            "get_model_variant",
            "get_runtime_settings",
            "resolve_recipe_preset",
            "save_runtime_settings",
            "switch_active_recipe",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(library_module, name))

    def test_search_api_has_no_backend_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "memesort_worker.app_commands._search_text"
        ) as search:
            with self.assertRaisesRegex(TypeError, "unexpected keyword argument"):
                search_text(
                    Path(temp_dir) / "library",
                    query="test",
                    top_k=3,
                    backend_name="debug",
                )
        search.assert_not_called()

    def test_embedding_factory_has_no_backend_selector(self) -> None:
        for backend_name in ("debug", "qwen3-vl", "cpu", "cuda"):
            with self.subTest(backend_name=backend_name):
                with self.assertRaisesRegex(TypeError, "positional argument"):
                    get_embedding_backend(backend_name)

    def test_recipe_change_atomically_resets_semantic_state_and_requeues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            source = Path(temp_dir) / "source"
            source.mkdir()
            (source / "image.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"test-image"
            )
            initialize_library(root)
            imported = import_folder(root, source)
            self.assertEqual(1, imported.new_assets)
            database = root / "library.sqlite"
            conn = sqlite3.connect(database)
            try:
                asset_id = str(conn.execute("SELECT id FROM asset").fetchone()[0])
                current_recipe = str(
                    conn.execute(
                        "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state "
                        "WHERE key = 'active_recipe_id'"
                    ).fetchone()[0]
                )
                old_recipe = str(uuid.uuid4())
                with conn:
                    conn.execute(
                        """
                        INSERT INTO embedding_recipe (
                            id, family_key, model_id, model_revision, output_dimension,
                            runtime_profile, preprocess_version, instruction_key,
                            pooling_key, normalized, gif_frame_count, created_at
                        )
                        SELECT ?, family_key, model_id, 'old-fingerprint', output_dimension,
                               'legacy', preprocess_version, instruction_key,
                               pooling_key, normalized, gif_frame_count, created_at
                        FROM embedding_recipe WHERE id = ?
                        """,
                        (old_recipe, current_recipe),
                    )
                    conn.execute(
                        "UPDATE job SET recipe_id = ? WHERE type = 'embed_asset'",
                        (old_recipe,),
                    )
                    conn.execute(
                        """
                        INSERT INTO embedding_item (
                            id, asset_id, recipe_id, kind, source_ref,
                            vector_dim, vector_blob, created_at
                        ) VALUES (?, ?, ?, 'image', 'old', 1, ?, 'old')
                        """,
                        (str(uuid.uuid4()), asset_id, old_recipe, b"\x00\x00\x80?"),
                    )
                    conn.execute(
                        "UPDATE worker_state SET value_json = ? WHERE key = 'active_recipe_id'",
                        (json.dumps({"recipe_id": old_recipe}),),
                    )
                    conn.execute(
                        "UPDATE worker_state SET value_json = ? "
                        "WHERE key = 'semantic_recipe_activation'",
                        (
                            json.dumps(
                                {
                                    "recipe_fingerprint": "old-fingerprint",
                                    "recipe_id": old_recipe,
                                }
                            ),
                        ),
                    )
            finally:
                conn.close()

            with patch(
                "memesort_worker.job_queue.enqueue_embedding",
                side_effect=RuntimeError("queue failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "queue failed"):
                    initialize_library(root)
            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    1, conn.execute("SELECT COUNT(*) FROM embedding_item").fetchone()[0]
                )
                active_after_rollback = conn.execute(
                    "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state "
                    "WHERE key = 'active_recipe_id'"
                ).fetchone()[0]
                self.assertEqual(old_recipe, active_after_rollback)
            finally:
                conn.close()

            initialize_library(root)
            initialize_library(root)
            conn = sqlite3.connect(database)
            try:
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM asset").fetchone()[0])
                self.assertEqual(
                    1, conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0]
                )
                self.assertEqual(
                    0, conn.execute("SELECT COUNT(*) FROM embedding_item").fetchone()[0]
                )
                embed_jobs = conn.execute(
                    "SELECT status, recipe_id FROM job WHERE type = 'embed_asset'"
                ).fetchall()
                self.assertEqual(1, len(embed_jobs))
                self.assertEqual("pending", embed_jobs[0][0])
                self.assertNotEqual(old_recipe, embed_jobs[0][1])
                self.assertEqual(
                    1, conn.execute("SELECT COUNT(*) FROM embedding_recipe").fetchone()[0]
                )
                self.assertGreater(
                    conn.execute(
                        "SELECT COUNT(*) FROM job WHERE type != 'embed_asset'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_manifest_preprocessing_applies_exif_and_white_alpha(self) -> None:
        provider = default_provider()
        spec = provider.preprocess_spec_for_version(
            load_runtime_manifest().preprocessing.version
        )
        transparent = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        transparent_bytes = io.BytesIO()
        transparent.save(transparent_bytes, format="PNG")
        processed = preprocess_image_bytes(
            transparent_bytes.getvalue(),
            spec,
        )
        with Image.open(io.BytesIO(processed)) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((255, 255, 255), image.getpixel((0, 0)))

        oriented = Image.new("RGB", (2, 1), "red")
        exif = Image.Exif()
        exif[274] = 6
        oriented_bytes = io.BytesIO()
        oriented.save(oriented_bytes, format="JPEG", exif=exif)
        processed = preprocess_image_bytes(
            oriented_bytes.getvalue(),
            spec,
        )
        with Image.open(io.BytesIO(processed)) as image:
            self.assertEqual((1, 2), image.size)

    def test_library_import_does_not_read_manifest_file(self) -> None:
        """Importing memesort_worker.library must not perform file I/O on the manifest.

        This is a Phase 1 acceptance criterion: the manifest is read lazily
        via the recipe provider, not at module import time.
        """
        script = (
            "import builtins, sys\n"
            "_real_open = builtins.open\n"
            "_manifest_reads = []\n"
            "def _tracking_open(*args, **kwargs):\n"
            "    if args and 'runtime-manifest' in str(args[0]):\n"
            "        _manifest_reads.append(str(args[0]))\n"
            "    return _real_open(*args, **kwargs)\n"
            "builtins.open = _tracking_open\n"
            "import memesort_worker.library\n"
            "builtins.open = _real_open\n"
            "if _manifest_reads:\n"
            "    print(f'FAIL: manifest read at import: {_manifest_reads}', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, f"stderr: {result.stderr}")
        self.assertIn("OK", result.stdout)

    def test_search_image_path_preserves_custom_provider_recipe(self) -> None:
        """A custom-provider image query must not reactivate the default recipe."""
        from memesort_worker.retrieval_service import search_image_path

        default = default_provider()
        default_spec = next(iter(default.preprocess_specs_by_version.values()))
        custom_version = f"{default_spec.version}-provider-regression"
        custom_fingerprint = "provider-regression-fingerprint"
        custom_provider = replace(
            default,
            recipe_fingerprint=custom_fingerprint,
            manifest_recipe={
                **default.manifest_recipe,
                "model_revision": custom_fingerprint,
                "preprocess_version": custom_version,
            },
            preprocess_specs_by_version={
                custom_version: replace(default_spec, version=custom_version),
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            library_root = Path(temp_dir) / "library"
            source_root = Path(temp_dir) / "source"
            source_root.mkdir()
            Image.new("RGB", (10, 10), "red").save(source_root / "asset.png")
            initialize_library(library_root, provider=custom_provider)
            import_folder(library_root, source_root, provider=custom_provider)

            query_image = Path(temp_dir) / "query.png"
            Image.new("RGB", (10, 10), "blue").save(query_image, format="PNG")

            database = library_root / "library.sqlite"
            conn = sqlite3.connect(database)
            try:
                before_active_recipe = str(
                    conn.execute(
                        "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state "
                        "WHERE key = 'active_recipe_id'"
                    ).fetchone()[0]
                )
                before_activation = str(
                    conn.execute(
                        "SELECT value_json FROM worker_state "
                        "WHERE key = 'semantic_recipe_activation'"
                    ).fetchone()[0]
                )
                before_embed_jobs = conn.execute(
                    "SELECT id, recipe_id, status, payload_json FROM job "
                    "WHERE type = 'embed_asset' ORDER BY id"
                ).fetchall()
            finally:
                conn.close()

            result = search_image_path(
                library_root,
                query_image,
                provider=custom_provider,
            )
            self.assertEqual([], result.results)

            conn = sqlite3.connect(database)
            try:
                active_recipe = str(
                    conn.execute(
                        "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state "
                        "WHERE key = 'active_recipe_id'"
                    ).fetchone()[0]
                )
                activation = str(
                    conn.execute(
                        "SELECT value_json FROM worker_state "
                        "WHERE key = 'semantic_recipe_activation'"
                    ).fetchone()[0]
                )
                embed_jobs = conn.execute(
                    "SELECT id, recipe_id, status, payload_json FROM job "
                    "WHERE type = 'embed_asset' ORDER BY id"
                ).fetchall()
            finally:
                conn.close()

        self.assertEqual(before_active_recipe, active_recipe)
        self.assertEqual(before_activation, activation)
        self.assertEqual(before_embed_jobs, embed_jobs)


if __name__ == "__main__":
    unittest.main()
