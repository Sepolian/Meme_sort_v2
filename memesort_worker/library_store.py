from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import memesort_worker.library as library
from . import ocr_artifacts


@dataclass(frozen=True)
class ActiveIndexRecipe:
    recipe_id: str
    label: str
    output_dimension: int
    instruction_text: str
    preprocess_version: str
    gif_frame_count: int


class LibraryStore:
    """Persistence interface for library state that callers should not assemble from private helpers."""

    def __init__(self, library_root: Path | str) -> None:
        init_result = library.initialize_library(library_root)
        self.library_root_path = Path(init_result.library_root)
        self.conn = library._connect(library._database_path(self.library_root_path))
        active_recipe_id = library._get_active_recipe_id(self.conn)
        recipe_row = library._get_recipe_row(self.conn, active_recipe_id)
        self.active_recipe = ActiveIndexRecipe(
            recipe_id=active_recipe_id,
            label=library._recipe_label(
                str(recipe_row["model_id"]),
                int(recipe_row["output_dimension"]),
                str(recipe_row["runtime_profile"]),
                library._gif_frame_count_for_recipe(recipe_row),
            ),
            output_dimension=int(recipe_row["output_dimension"]),
            instruction_text=library._instruction_text_for_key(str(recipe_row["instruction_key"])),
            preprocess_version=str(recipe_row["preprocess_version"]),
            gif_frame_count=library._gif_frame_count_for_recipe(recipe_row),
        )

    def __enter__(self) -> "LibraryStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def get_worker_state_json(self, key: str) -> dict[str, object] | None:
        return library._get_worker_state_json(self.conn, key)

    def set_worker_state_json(self, key: str, payload: dict[str, object]) -> None:
        with self.conn:
            library._set_worker_state_json(self.conn, key, payload)

    def collect_active_vector_rows(
        self,
        asset_id_to_exclude: str | None = None,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT ei.asset_id, ei.vector_dim, ei.vector_blob, ei.source_ref, a.library_path, a.media_type, a.content_hash
            FROM embedding_item ei
            JOIN asset a ON a.id = ei.asset_id
            WHERE ei.recipe_id = ?
              AND ei.kind = 'image'
              AND a.deleted_at IS NULL
        """
        params: list[object] = [self.active_recipe.recipe_id]
        if asset_id_to_exclude is not None:
            query += "\n              AND ei.asset_id <> ?"
            params.append(asset_id_to_exclude)
        query += "\n            ORDER BY ei.created_at ASC, ei.id ASC"
        return self.conn.execute(query, tuple(params)).fetchall()

    def collect_asset_embedding_rows(self, asset_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT vector_blob, vector_dim
            FROM embedding_item
            WHERE asset_id = ?
              AND recipe_id = ?
              AND kind = 'image'
            """,
            (asset_id, self.active_recipe.recipe_id),
        ).fetchall()

    def collect_ocr_search_rows(self, query: str, limit: int) -> list[sqlite3.Row]:
        return ocr_artifacts.collect_ocr_search_rows(self.conn, query, limit)

    def collect_ocr_search_results(self, query: str, limit: int) -> list[dict[str, object]]:
        rows = self.collect_ocr_search_rows(query, limit)
        return ocr_artifacts.project_ocr_search_rows(query, rows)
