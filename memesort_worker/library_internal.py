"""Internal seam for specialist modules that need Library implementation details.

``memesort_worker.library`` remains the compatibility interface used by the CLI,
web app, evaluation scripts, and existing debugging workflows. Specialist modules
must import this module instead, so their required implementation details are
explicit and can later move without expanding the compatibility interface.
"""

from __future__ import annotations

from . import library as _implementation


__all__ = [
    "AssetListResult",
    "INSTRUCTION_TEXT_BY_KEY",
    "ImageSearchResult",
    "LibraryStatusResult",
    "MANIFEST_MODEL_KEY",
    "RunJobsResult",
    "RuntimeHealthResult",
    "RuntimeSettings",
    "SUPPORTED_EXTENSIONS",
    "SearchResult",
    "SetupStateResult",
    "SimilarityResult",
    "_connect",
    "_database_path",
    "_extract_gif_frame_bytes",
    "_get_active_recipe_id",
    "_get_recipe_row",
    "_get_worker_state_json",
    "_gif_frame_count_for_recipe",
    "_instruction_text_for_key",
    "_preprocess_image_bytes",
    "_recipe_label",
    "_set_worker_state_json",
    "_utc_now",
    "discover_local_gguf_model_path",
    "get_embedding_backend",
    "get_model_variant",
    "get_runtime_profile",
    "get_runtime_settings",
    "import_folder",
    "initialize_library",
    "is_runtime_ready_for_indexing",
    "list_assets",
    "resolve_recipe_preset",
    "runtime_health_matches_settings",
    "switch_active_recipe",
]


def __getattr__(name: str) -> object:
    """Resolve allowed implementation details lazily.

    Lazy lookup deliberately preserves existing test patches against the
    compatibility module while callers migrate to this internal seam.
    """
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted([*globals(), *__all__])
