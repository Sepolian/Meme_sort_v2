from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from memesort_worker.active_runtime import search_text_for_active_runtime
from memesort_worker.embedding_backend import get_embedding_backend
from memesort_worker.library import (
    get_runtime_settings,
    initialize_library,
    import_folder,
    list_assets,
    list_model_variants,
    list_runtime_profiles,
    save_runtime_settings,
    _preprocess_image_bytes,
)
from memesort_worker.runtime_manifest import load_runtime_manifest


class VulkanOnlyRuntimeTests(unittest.TestCase):
    def test_only_manifest_vulkan_runtime_is_exposed(self) -> None:
        manifest = load_runtime_manifest()
        profiles = list_runtime_profiles()
        models = list_model_variants()

        self.assertEqual(["vulkan"], [profile.profile_id for profile in profiles])
        self.assertEqual(["manifest"], [model.model_key for model in models])
        self.assertEqual("llama.cpp", profiles[0].backend_name)
        self.assertEqual("Vulkan0", profiles[0].device)
        self.assertEqual(manifest.model.id, models[0].model_id)
        self.assertEqual(manifest.model.output_dimension, models[0].output_dimension)

    def test_new_library_uses_manifest_recipe_and_canonical_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            initialize_library(root)
            settings = get_runtime_settings(root)
            assets = list_assets(root)

        self.assertEqual("vulkan", settings.selected_profile)
        self.assertEqual("manifest", settings.selected_model_key)
        self.assertEqual("vulkan-manifest", settings.selected_recipe_preset)
        self.assertEqual("llama.cpp", settings.backend_name)
        self.assertIsNone(settings.model_name_or_path)
        self.assertIn(" / vulkan", assets.active_recipe_label)

    def test_custom_model_path_and_legacy_backend_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            with self.assertRaisesRegex(ValueError, "owned by runtime-manifest"):
                save_runtime_settings(
                    root,
                    selected_profile="vulkan",
                    selected_model_key="manifest",
                    model_name_or_path=r"C:\custom\model.gguf",
                )
            with self.assertRaisesRegex(ValueError, "Unknown runtime profile"):
                save_runtime_settings(
                    root,
                    selected_profile="cpu-low-memory",
                    selected_model_key="manifest",
                    model_name_or_path=None,
                )

    def test_search_api_has_no_backend_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "memesort_worker.active_runtime._search_text"
        ) as search:
            with self.assertRaisesRegex(TypeError, "unexpected keyword argument"):
                search_text_for_active_runtime(
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
                "memesort_worker.library._create_job",
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
        transparent = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        transparent_bytes = io.BytesIO()
        transparent.save(transparent_bytes, format="PNG")
        processed = _preprocess_image_bytes(
            transparent_bytes.getvalue(),
            load_runtime_manifest().preprocessing.version,
        )
        with Image.open(io.BytesIO(processed)) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((255, 255, 255), image.getpixel((0, 0)))

        oriented = Image.new("RGB", (2, 1), "red")
        exif = Image.Exif()
        exif[274] = 6
        oriented_bytes = io.BytesIO()
        oriented.save(oriented_bytes, format="JPEG", exif=exif)
        processed = _preprocess_image_bytes(
            oriented_bytes.getvalue(),
            load_runtime_manifest().preprocessing.version,
        )
        with Image.open(io.BytesIO(processed)) as image:
            self.assertEqual((1, 2), image.size)


if __name__ == "__main__":
    unittest.main()
