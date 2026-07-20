"""Indexing Store: narrow write adapter for job execution.

This module provides a focused interface for the indexing pipeline to read
asset/recipe data and write derived artifacts (thumbnails, embeddings).
It hides the database schema and connection management from callers.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import asset_catalog
from .semantic_retrieval import vector_to_blob


@dataclass(frozen=True)
class RecipeRow:
    """Immutable snapshot of an embedding recipe."""

    recipe_id: str
    model_id: str
    output_dimension: int
    runtime_profile: str
    preprocess_version: str
    instruction_key: str
    gif_frame_count: int | None


class IndexingStore:
    """Narrow write adapter for indexing job execution.

    Provides only the read/write operations needed by the indexing pipeline,
    without exposing the raw database connection or schema details.
    """

    def __init__(self, library_root: Path) -> None:
        self._library_root = library_root
        self._conn = asset_catalog.connect(
            asset_catalog.database_path(library_root)
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IndexingStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose connection for JobQueue (temporary compatibility)."""
        return self._conn

    def get_asset_library_path(self, asset_id: str) -> Path | None:
        """Return the library-relative path for an asset, or None if not found."""
        row = self._conn.execute(
            """
            SELECT library_path
            FROM asset
            WHERE id = ?
              AND deleted_at IS NULL
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        return self._library_root / str(row["library_path"])

    def get_recipe(self, recipe_id: str) -> RecipeRow:
        """Return recipe info by ID."""
        row = asset_catalog.get_recipe_row(self._conn, recipe_id)
        return RecipeRow(
            recipe_id=str(row["id"]),
            model_id=str(row["model_id"]),
            output_dimension=int(row["output_dimension"]),
            runtime_profile=str(row["runtime_profile"]),
            preprocess_version=str(row["preprocess_version"]),
            instruction_key=str(row["instruction_key"]),
            gif_frame_count=row["gif_frame_count"],
        )

    def upsert_thumbnail_rendition(
        self,
        asset_id: str,
        width: int,
        height: int,
    ) -> None:
        """Create or update the thumbnail rendition for an asset."""
        now = asset_catalog.utc_now()
        existing = self._conn.execute(
            """
            SELECT id
            FROM rendition
            WHERE asset_id = ?
              AND kind = 'thumbnail'
            """,
            (asset_id,),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO rendition (id, asset_id, kind, path, width, height, frame_index, created_at)
                VALUES (?, ?, 'thumbnail', ?, ?, ?, NULL, ?)
                """,
                (
                    str(uuid.uuid4()),
                    asset_id,
                    f"thumbnails/{asset_id}.jpg",
                    width,
                    height,
                    now,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE rendition
                SET path = ?, width = ?, height = ?, created_at = ?
                WHERE id = ?
                """,
                (
                    f"thumbnails/{asset_id}.jpg",
                    width,
                    height,
                    now,
                    str(existing["id"]),
                ),
            )
        self.touch_asset(asset_id)

    def has_embedding(self, asset_id: str, recipe_id: str) -> bool:
        """Check if an embedding already exists for this asset/recipe."""
        row = self._conn.execute(
            """
            SELECT id
            FROM embedding_item
            WHERE asset_id = ?
              AND recipe_id = ?
              AND kind = 'image'
            LIMIT 1
            """,
            (asset_id, recipe_id),
        ).fetchone()
        return row is not None

    def insert_embedding(
        self,
        asset_id: str,
        recipe_id: str,
        output_dimension: int,
        source_ref: str,
        vector: np.ndarray,
    ) -> None:
        """Insert an embedding vector for an asset."""
        if vector.shape[0] != output_dimension:
            from .embedding_backend import EmbeddingBackendError
            raise EmbeddingBackendError(
                f"Embedding backend returned dim {vector.shape[0]}, expected {output_dimension}"
            )
        self._conn.execute(
            """
            INSERT INTO embedding_item (
                id,
                asset_id,
                recipe_id,
                kind,
                source_ref,
                vector_dim,
                vector_blob,
                created_at
            )
            VALUES (?, ?, ?, 'image', ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                asset_id,
                recipe_id,
                source_ref,
                output_dimension,
                sqlite3.Binary(vector_to_blob(vector)),
                asset_catalog.utc_now(),
            ),
        )

    def touch_asset(self, asset_id: str) -> None:
        """Update the asset's updated_at timestamp."""
        self._conn.execute(
            """
            UPDATE asset
            SET updated_at = ?
            WHERE id = ?
            """,
            (asset_catalog.utc_now(), asset_id),
        )
