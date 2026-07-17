from __future__ import annotations

from dataclasses import asdict, dataclass

from .runtime_manifest import load_runtime_manifest


@dataclass(frozen=True)
class RuntimeDescriptor:
    """Read-only public description of the manifest-owned inference runtime."""

    backend_name: str
    device: str
    llama_cpp_build: str
    model_id: str
    model_label: str
    output_dimension: int
    storage_dtype: str
    runtime_fingerprint: str
    recipe_fingerprint: str
    preprocessing_version: str
    still_max_side: int
    gif_max_side: int
    gif_frame_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def get_runtime_descriptor() -> RuntimeDescriptor:
    manifest = load_runtime_manifest()
    return RuntimeDescriptor(
        backend_name="llama.cpp",
        device=manifest.platform.device,
        llama_cpp_build=manifest.llama_cpp.build,
        model_id=manifest.model.id,
        model_label=manifest.model.base_model_id.split("/")[-1],
        output_dimension=manifest.model.output_dimension,
        storage_dtype=manifest.embedding.storage_dtype,
        runtime_fingerprint=manifest.runtime_fingerprint,
        recipe_fingerprint=manifest.recipe_fingerprint,
        preprocessing_version=manifest.preprocessing.version,
        still_max_side=manifest.preprocessing.still_max_side,
        gif_max_side=manifest.preprocessing.gif_max_side,
        gif_frame_count=manifest.preprocessing.gif_frame_count,
    )
