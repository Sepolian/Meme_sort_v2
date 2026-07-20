"""Runtime recipe provider: manifest-derived immutable specs for asset processing.

This module encapsulates the data that library, indexing and retrieval need
from the runtime manifest.  Constructing a provider reads and validates the
manifest; importing this module does **not**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .runtime_manifest import RuntimeManifest, load_runtime_manifest


VULKAN_PROFILE_ID = "vulkan"


@dataclass(frozen=True)
class PreprocessSpec:
    """Immutable preprocessing parameters for one recipe version."""

    version: str
    still_max_side: int
    gif_max_side: int
    gif_frame_count: int
    color_mode: str
    alpha_background: tuple[int, int, int]


@dataclass(frozen=True)
class RuntimeRecipeProvider:
    """Manifest-derived recipe and preprocessing specs.

    Construct via :func:`from_manifest` or :func:`default_provider`.
    All fields are immutable; the provider is safe to share across threads.
    """

    recipe_fingerprint: str
    manifest_recipe: Mapping[str, object]
    instruction_text_by_key: Mapping[str, str]
    preprocess_specs_by_version: Mapping[str, PreprocessSpec]
    default_gif_frame_count: int

    def instruction_text_for_key(self, instruction_key: str) -> str:
        instruction = self.instruction_text_by_key.get(instruction_key)
        if instruction is None:
            raise ValueError(f"Unsupported instruction key: {instruction_key}")
        return instruction

    def preprocess_spec_for_version(self, preprocess_version: str) -> PreprocessSpec:
        spec = self.preprocess_specs_by_version.get(preprocess_version)
        if spec is None:
            raise ValueError(f"Unsupported preprocess version: {preprocess_version}")
        return spec


def _parse_alpha_background(raw: str) -> tuple[int, int, int]:
    """Parse '#rrggbb' hex string to RGB tuple."""
    raw = raw.lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Invalid alpha_background: #{raw}")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def from_manifest(manifest: RuntimeManifest) -> RuntimeRecipeProvider:
    """Build a provider from an already-loaded manifest."""
    pooling_key = (
        f"{manifest.embedding.pooling}-"
        f"{manifest.embedding.normalization}-"
        f"{manifest.embedding.storage_dtype}"
    )
    manifest_recipe: dict[str, object] = {
        "family_key": manifest.model.protocol,
        "model_id": manifest.model.id,
        "model_revision": manifest.recipe_fingerprint,
        "output_dimension": manifest.model.output_dimension,
        "runtime_profile": VULKAN_PROFILE_ID,
        "preprocess_version": manifest.preprocessing.version,
        "instruction_key": manifest.embedding.instruction_id,
        "pooling_key": pooling_key,
        "normalized": 1,
        "gif_frame_count": manifest.preprocessing.gif_frame_count,
    }
    preprocess_spec = PreprocessSpec(
        version=manifest.preprocessing.version,
        still_max_side=manifest.preprocessing.still_max_side,
        gif_max_side=manifest.preprocessing.gif_max_side,
        gif_frame_count=manifest.preprocessing.gif_frame_count,
        color_mode=manifest.preprocessing.color_mode,
        alpha_background=_parse_alpha_background(manifest.preprocessing.alpha_background),
    )
    return RuntimeRecipeProvider(
        recipe_fingerprint=manifest.recipe_fingerprint,
        manifest_recipe=manifest_recipe,
        instruction_text_by_key={
            manifest.embedding.instruction_id: manifest.embedding.instruction,
        },
        preprocess_specs_by_version={
            manifest.preprocessing.version: preprocess_spec,
        },
        default_gif_frame_count=manifest.preprocessing.gif_frame_count,
    )


_default_provider: RuntimeRecipeProvider | None = None


def default_provider() -> RuntimeRecipeProvider:
    """Return the singleton provider built from the project manifest.

    The manifest is read on first call, not at import time.
    """
    global _default_provider
    if _default_provider is None:
        _default_provider = from_manifest(load_runtime_manifest())
    return _default_provider


def reset_default_provider() -> None:
    """Clear the cached default provider (for testing)."""
    global _default_provider
    _default_provider = None
