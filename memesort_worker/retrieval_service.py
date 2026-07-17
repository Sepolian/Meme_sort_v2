from __future__ import annotations

import uuid
from pathlib import Path

from . import library_internal as library
from .library_store import LibraryStore
from .inference_service import search_inference_request
from .retrieval_composition import compose_text_search_results
from .semantic_retrieval import blob_to_vector, rank_asset_vector_rows


def search_text(
    library_root: Path | str,
    query: str,
    top_k: int = 10,
    request_id: str | None = None,
) -> library.SearchResult:
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    with LibraryStore(library_root) as store:
        vector_rows = store.collect_active_vector_rows()
        ocr_results = store.collect_ocr_search_results(query, limit=max(top_k * 4, 20))

        if not vector_rows:
            visual_results: list[dict[str, object]] = []
        else:
            backend = library.get_embedding_backend()
            with search_inference_request(request_id or str(uuid.uuid4())):
                query_vector = backend.embed_text(
                    query,
                    store.active_recipe.output_dimension,
                    instruction=store.active_recipe.instruction_text,
                )
            visual_results = rank_asset_vector_rows(
                query_vector,
                vector_rows,
                max(top_k * 4, 20),
            )
        results = compose_text_search_results(visual_results, ocr_results, top_k)

        return library.SearchResult(
            library_root=str(store.library_root_path),
            active_recipe_id=store.active_recipe.recipe_id,
            active_recipe_label=store.active_recipe.label,
            query=query,
            top_k=top_k,
            results=results,
        )


def search_image_path(
    library_root: Path | str,
    image_path: Path | str,
    top_k: int = 10,
    request_id: str | None = None,
) -> library.ImageSearchResult:
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    query_path = Path(image_path).expanduser().resolve()
    if not query_path.exists() or not query_path.is_file():
        raise ValueError(f"Image file does not exist: {query_path}")
    suffix = query_path.suffix.lower()
    if suffix not in library.SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported image file extension: {suffix or '(none)'}")

    with LibraryStore(library_root) as store:
        vector_rows = store.collect_active_vector_rows()

        if not vector_rows:
            results: list[dict[str, object]] = []
        else:
            backend = library.get_embedding_backend()
            with search_inference_request(request_id or str(uuid.uuid4())):
                image_bytes = query_path.read_bytes()
                if suffix == ".gif":
                    frame_payloads = library._extract_gif_frame_bytes(
                        image_bytes,
                        store.active_recipe.preprocess_version,
                        frame_count=store.active_recipe.gif_frame_count,
                    )
                    query_vectors = [
                        backend.embed_image_bytes(
                            frame_bytes,
                            store.active_recipe.output_dimension,
                            instruction=store.active_recipe.instruction_text,
                        )
                        for _, frame_bytes in frame_payloads
                    ]
                else:
                    processed_bytes = library._preprocess_image_bytes(
                        image_bytes,
                        store.active_recipe.preprocess_version,
                    )
                    query_vectors = [
                        backend.embed_image_bytes(
                            processed_bytes,
                            store.active_recipe.output_dimension,
                            instruction=store.active_recipe.instruction_text,
                        )
                    ]
            vector_query = query_vectors if len(query_vectors) > 1 else query_vectors[0]
            results = rank_asset_vector_rows(vector_query, vector_rows, top_k)

        return library.ImageSearchResult(
            library_root=str(store.library_root_path),
            active_recipe_id=store.active_recipe.recipe_id,
            active_recipe_label=store.active_recipe.label,
            query_path=str(query_path),
            query_media_type=library.SUPPORTED_EXTENSIONS[suffix],
            top_k=top_k,
            results=results,
        )


def find_similar_assets(
    library_root: Path | str,
    asset_id: str,
    top_k: int = 10,
) -> library.SimilarityResult:
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    with LibraryStore(library_root) as store:
        query_rows = store.collect_asset_embedding_rows(asset_id)
        if not query_rows:
            raise ValueError(
                f"Asset {asset_id} has no active embedding for recipe {store.active_recipe.recipe_id}"
            )

        vector_rows = store.collect_active_vector_rows(asset_id_to_exclude=asset_id)
        query_vectors = [
            blob_to_vector(bytes(query_row["vector_blob"]), int(query_row["vector_dim"]))
            for query_row in query_rows
        ]
        results = rank_asset_vector_rows(query_vectors, vector_rows, top_k)

        return library.SimilarityResult(
            library_root=str(store.library_root_path),
            active_recipe_id=store.active_recipe.recipe_id,
            active_recipe_label=store.active_recipe.label,
            asset_id=asset_id,
            top_k=top_k,
            results=results,
        )
