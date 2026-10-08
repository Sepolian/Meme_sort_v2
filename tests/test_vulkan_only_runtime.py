from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from memesort_worker.asset_preprocessing import preprocess_image_bytes
from memesort_worker.asset_catalog import initialize_library, import_folder
from memesort_worker.recipe_provider import default_provider
from memesort_worker.runtime_descriptor import get_runtime_descriptor
from memesort_worker.runtime_manifest import load_runtime_manifest
from runtime_fakes import FakeIndexingRuntime


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

    def test_qwen_to_gemma_activation_preserves_library_and_rolls_back_queue_failure(self) -> None:
        from memesort_worker.asset_catalog import accept_duplicate_pair
        from memesort_worker.indexing_pipeline import run_pending_jobs
        from memesort_worker.library_store import LibraryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "library"
            source = Path(temp_dir) / "source"
            source.mkdir()
            Image.new("RGB", (40, 30), "red").save(source / "red.png")
            Image.new("RGB", (40, 30), "blue").save(source / "blue.png")
            (source / "red-copy.png").write_bytes((source / "red.png").read_bytes())
            imported = import_folder(root, source)
            self.assertEqual(2, imported.new_assets)
            self.assertEqual(1, imported.duplicate_assets)
            self.assertEqual(0, run_pending_jobs(root, FakeIndexingRuntime()).failed_jobs)
            with LibraryStore(root) as store:
                before_assets = store.list_assets_detailed().assets
                self.assertEqual(1, len(store.scan_duplicate_assets().pairs))
            asset_ids = [str(asset["asset_id"]) for asset in before_assets]
            accept_duplicate_pair(root, *asset_ids)
            copies = {
                str(asset["asset_id"]): (root / str(asset["library_path"])).read_bytes()
                for asset in before_assets
            }

            # Fixture: an existing Qwen Library with its incompatible 2048d vectors.
            old_recipe = str(uuid.uuid4())
            old_vector = np.zeros(2048, dtype=np.float32)
            old_vector[0] = 1.0
            database = root / "library.sqlite"
            conn = sqlite3.connect(database)
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO embedding_recipe (
                            id, family_key, model_id, model_revision, output_dimension,
                            runtime_profile, preprocess_version, instruction_key,
                            pooling_key, normalized, gif_frame_count, created_at
                        )
                        SELECT ?, family_key, 'DevQuasar/Qwen.Qwen3-VL-Embedding-2B-GGUF:Q4_K_M',
                               'old-qwen-fingerprint', 2048, runtime_profile, preprocess_version,
                               'qwen3vl-text-to-image-default-v1', 'last-l2-float32',
                               normalized, gif_frame_count, created_at
                        FROM embedding_recipe WHERE id = ?
                        """,
                        (old_recipe, imported.active_recipe_id),
                    )
                    conn.execute(
                        "UPDATE embedding_item SET recipe_id = ?, vector_dim = 2048, vector_blob = ?",
                        (old_recipe, old_vector.tobytes()),
                    )
                    conn.execute(
                        "UPDATE job SET recipe_id = ?, status = 'failed', "
                        "payload_json = json_set(payload_json, '$.recipe_id', ?) "
                        "WHERE type = 'embed_asset'",
                        (old_recipe, old_recipe),
                    )
                    conn.execute(
                        "UPDATE worker_state SET value_json = ? WHERE key = 'active_recipe_id'",
                        (json.dumps({"recipe_id": old_recipe}),),
                    )
                    conn.execute(
                        "UPDATE worker_state SET value_json = ? "
                        "WHERE key = 'semantic_recipe_activation'",
                        (json.dumps({"recipe_fingerprint": "old-qwen-fingerprint", "recipe_id": old_recipe}),),
                    )
                before_vectors = conn.execute(
                    "SELECT id, asset_id, recipe_id, vector_dim, vector_blob FROM embedding_item ORDER BY id"
                ).fetchall()
                before_jobs = conn.execute(
                    "SELECT id, recipe_id, status, payload_json FROM job ORDER BY id"
                ).fetchall()
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
                self.assertEqual(before_vectors, conn.execute(
                    "SELECT id, asset_id, recipe_id, vector_dim, vector_blob FROM embedding_item ORDER BY id"
                ).fetchall())
                self.assertEqual(before_jobs, conn.execute(
                    "SELECT id, recipe_id, status, payload_json FROM job ORDER BY id"
                ).fetchall())
                self.assertEqual(old_recipe, conn.execute(
                    "SELECT json_extract(value_json, '$.recipe_id') FROM worker_state "
                    "WHERE key = 'active_recipe_id'"
                ).fetchone()[0])
            finally:
                conn.close()

            initialized = initialize_library(root)
            with LibraryStore(root) as store:
                recipe_id = store.active_recipe.recipe_id
                self.assertEqual(initialized.created_recipe_id, recipe_id)
                self.assertNotEqual(old_recipe, recipe_id)
                self.assertEqual(768, store.active_recipe.output_dimension)
                self.assertEqual("task: search result | query: ", store.active_recipe.instruction_text)
                self.assertEqual([], store.list_active_embeddings())
                pending = store.list_pending_jobs()
                self.assertEqual(2, len(pending))
                self.assertEqual({"embed_asset"}, {job["type"] for job in pending})
                self.assertEqual(set(asset_ids), {job["asset_id"] for job in pending})
                after_assets = store.list_assets_detailed().assets
            before_by_id = {str(asset["asset_id"]): asset for asset in before_assets}
            for asset in after_assets:
                before = before_by_id[str(asset["asset_id"])]
                for key in ("library_path", "content_hash", "source_records", "ocr_status", "ocr_results", "renditions"):
                    self.assertEqual(before[key], asset[key], key)
                self.assertEqual("pending_initial_index", asset["status"])
                self.assertEqual(copies[str(asset["asset_id"])], (root / str(asset["library_path"])).read_bytes())

            initialize_library(root)
            with LibraryStore(root) as store:
                self.assertEqual(recipe_id, store.active_recipe.recipe_id)
                self.assertEqual(pending, store.list_pending_jobs())
            conn = sqlite3.connect(database)
            try:
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM embedding_item").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM embedding_recipe").fetchone()[0])
            finally:
                conn.close()

            reindexed = run_pending_jobs(root, FakeIndexingRuntime())
            self.assertEqual(2, reindexed.completed_jobs)
            self.assertEqual(0, reindexed.failed_jobs)
            with LibraryStore(root) as store:
                self.assertEqual({"indexed"}, {asset["status"] for asset in store.list_assets_detailed().assets})
                self.assertEqual(0.92, store.scan_duplicate_assets().threshold)
                self.assertEqual([], store.scan_duplicate_assets().pairs)
                for embedding in store.list_active_embeddings():
                    self.assertEqual((768,), embedding.vector.shape)
                    self.assertEqual(np.dtype("float32"), embedding.vector.dtype)
                    self.assertTrue(np.isfinite(embedding.vector).all())
                    self.assertAlmostEqual(1.0, float(np.linalg.norm(embedding.vector)), places=6)
            self.assertTrue(accept_duplicate_pair(root, *asset_ids).already_accepted)

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

    def _index_custom_recipe(self, root: Path):
        from memesort_worker.indexing_pipeline import run_pending_jobs

        default = default_provider()
        fingerprint = "retrieval-provider-regression"
        provider = replace(
            default,
            recipe_fingerprint=fingerprint,
            manifest_recipe={**default.manifest_recipe, "model_revision": fingerprint},
        )
        library_root = root / "library"
        source_root = root / "source"
        source_root.mkdir()
        Image.new("RGB", (10, 10), "red").save(source_root / "red.png")
        Image.new("RGB", (10, 10), "blue").save(source_root / "blue.png")
        imported = import_folder(library_root, source_root, provider=provider)
        result = run_pending_jobs(library_root, FakeIndexingRuntime(), provider=provider)
        self.assertEqual(0, result.failed_jobs)
        return library_root, provider, imported.active_recipe_id

    def test_text_search_preserves_custom_provider_recipe(self) -> None:
        from memesort_worker.library_store import LibraryStore
        from memesort_worker.retrieval_service import search_text

        with tempfile.TemporaryDirectory() as temp_dir:
            root, provider, recipe_id = self._index_custom_recipe(Path(temp_dir))
            result = search_text(
                root,
                "reaction",
                provider=provider,
                runtime=FakeIndexingRuntime(),
            )
            self.assertEqual(recipe_id, result.active_recipe_id)
            self.assertEqual(2, len(result.results))
            with LibraryStore(root, provider=provider) as store:
                self.assertEqual(2, len(store.list_active_embeddings()))
                self.assertEqual(0, len(store.list_pending_jobs()))

    def test_similar_assets_preserve_custom_provider_recipe(self) -> None:
        from memesort_worker.library_store import LibraryStore
        from memesort_worker.retrieval_service import find_similar_assets

        with tempfile.TemporaryDirectory() as temp_dir:
            root, provider, recipe_id = self._index_custom_recipe(Path(temp_dir))
            with LibraryStore(root, provider=provider) as store:
                asset_id = str(store.list_assets_detailed().assets[0]["asset_id"])
            result = find_similar_assets(root, asset_id, provider=provider)
            self.assertEqual(recipe_id, result.active_recipe_id)
            self.assertEqual(1, len(result.results))
            self.assertNotEqual(asset_id, result.results[0]["asset_id"])
            with LibraryStore(root, provider=provider) as store:
                self.assertEqual(2, len(store.list_active_embeddings()))
                self.assertEqual(0, len(store.list_pending_jobs()))

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
                runtime=FakeIndexingRuntime(),
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
