from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable

from . import library
from .library_store import LibraryStore
from .runtime_admission import (
    crosscheck_llama_vulkan0,
    probe_vulkan0,
    validate_pinned_runtime_files,
)
from .runtime_activation import validate_runtime_activation
from .runtime_manifest import load_runtime_manifest


_HEALTH_CHECK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


class RuntimeAuthorizationError(RuntimeError):
    """The Pinned Runtime did not authorize work in this application session."""


def _save_last_health_check(
    library_root: Path | str,
    result: library.RuntimeHealthResult,
) -> None:
    with LibraryStore(library_root) as store:
        store.set_worker_state_json("last_runtime_health_check", result.to_dict())


def runtime_health_matches_manifest(
    health_check: library.RuntimeHealthResult,
) -> bool:
    return health_check.runtime_fingerprint == load_runtime_manifest().runtime_fingerprint


def get_last_health_check(
    library_root: Path | str,
) -> library.RuntimeHealthResult | None:
    with LibraryStore(library_root) as store:
        payload = store.get_worker_state_json("last_runtime_health_check")
        if payload is None or not payload.get("runtime_fingerprint"):
            return None
        return library.RuntimeHealthResult(
            runtime_fingerprint=str(payload["runtime_fingerprint"]),
            backend_name=str(payload["backend_name"]),
            device=str(payload["device"]),
            gpu_name=str(payload["gpu_name"]) if payload.get("gpu_name") else None,
            gpu_vendor=(
                str(payload["gpu_vendor"]) if payload.get("gpu_vendor") else None
            ),
            gpu_vendor_id=(
                str(payload["gpu_vendor_id"])
                if payload.get("gpu_vendor_id")
                else None
            ),
            text_smoke_vector_dim=(
                int(payload["text_smoke_vector_dim"])
                if payload.get("text_smoke_vector_dim") is not None
                else None
            ),
            image_smoke_vector_dim=(
                int(payload["image_smoke_vector_dim"])
                if payload.get("image_smoke_vector_dim") is not None
                else None
            ),
            diagnostic_steps=list(payload.get("diagnostic_steps", [])),
            smoke_test_ok=bool(payload["smoke_test_ok"]),
            error=str(payload["error"]) if payload.get("error") else None,
        )


def run_runtime_health_check(
    library_root: Path | str | None = None,
    embedding_backend_factory: Callable[[], object] | None = None,
) -> library.RuntimeHealthResult:
    manifest = load_runtime_manifest()
    diagnostic_steps: list[dict[str, object]] = [
        {
            "step": "runtime-manifest",
            "status": "ok",
            "detail": manifest.runtime_fingerprint,
        },
    ]

    return _run_llama_cpp_runtime_health_check(
        manifest=manifest,
        library_root=library_root,
        diagnostic_steps=diagnostic_steps,
        embedding_backend_factory=embedding_backend_factory,
    )


def _create_owned_embedding_backend():
    from .embedding_backend import LlamaCppEmbeddingBackend
    from .inference_service import InferenceScheduler

    return LlamaCppEmbeddingBackend(InferenceScheduler())


def _run_llama_cpp_runtime_health_check(
    manifest,
    library_root: Path | str | None,
    diagnostic_steps: list[dict[str, object]],
    embedding_backend_factory: Callable[[], object] | None = None,
) -> library.RuntimeHealthResult:
    if not manifest.main_model_path.is_file() or not manifest.projector_path.is_file():
        result = library.RuntimeHealthResult(
            runtime_fingerprint=manifest.runtime_fingerprint,
            backend_name="llama.cpp",
            device=manifest.platform.device,
            gpu_name=None,
            gpu_vendor=None,
            gpu_vendor_id=None,
            text_smoke_vector_dim=None,
            image_smoke_vector_dim=None,
            diagnostic_steps=[
                *diagnostic_steps,
                {
                    "step": "resolve-gguf-bundle",
                    "status": "error",
                    "detail": (
                        "The pinned GGUF bundle declared by runtime-manifest.json is "
                        "not active. Run setup to install it."
                    ),
                },
            ],
            smoke_test_ok=False,
            error="Pinned GGUF model bundle is missing.",
        )
        if library_root is not None:
            _save_last_health_check(library_root, result=result)
        return result

    failure_step = "resolve-gguf-bundle"
    gpu_name: str | None = None
    gpu_vendor: str | None = None
    gpu_vendor_id: str | None = None
    owned_backend = None
    try:
        from .llama_cpp_backend import (
            discover_llama_server,
            probe_llama_devices,
            verify_qwen3_vl_embedding_2b_bundle,
        )

        main_model = manifest.main_model_path
        mmproj = manifest.projector_path
        validate_runtime_activation(manifest)
        validate_pinned_runtime_files(manifest)
        verify_qwen3_vl_embedding_2b_bundle(main_model, mmproj, manifest)
        diagnostic_steps.append(
            {
                "step": "resolve-gguf-bundle",
                "status": "ok",
                "detail": f"{main_model.name} + {mmproj.name}",
            }
        )
        failure_step = "vulkan-device"
        vulkan_device = probe_vulkan0(manifest)
        gpu_vendor = vulkan_device.vendor_name
        gpu_vendor_id = vulkan_device.vendor_id_hex
        diagnostic_steps.append(
            {
                "step": "vulkan-device",
                "status": "ok",
                "detail": (
                    f"Vulkan0: {vulkan_device.device_name}; "
                    f"vendor={vulkan_device.vendor_name} "
                    f"({vulkan_device.vendor_id_hex})"
                ),
            }
        )
        failure_step = "llama-server"
        executable = discover_llama_server()
        server_detail = str(executable)
        device_output = probe_llama_devices(
            executable,
            timeout_seconds=manifest.llama_cpp.server.device_probe_timeout_seconds,
        )
        gpu_name = crosscheck_llama_vulkan0(
            vulkan_device,
            device_output,
            manifest.platform.device,
        )
        diagnostic_steps.append(
            {
                "step": "llama-server",
                "status": "ok",
                "detail": server_detail,
            }
        )
        failure_step = "text-embedding-smoke"
        if embedding_backend_factory is None:
            owned_backend = _create_owned_embedding_backend()
            backend = owned_backend
        else:
            backend = embedding_backend_factory()
        vector = backend.embed_text(
            "confused reaction image",
            output_dimension=manifest.model.output_dimension,
            instruction=manifest.embedding.instruction,
        )
        diagnostic_steps.append(
            {
                "step": "text-embedding-smoke",
                "status": "ok",
                "detail": f"llama.cpp text embedding passed at {int(vector.shape[0])}d.",
            }
        )
        failure_step = "image-embedding-smoke"
        image_vector = backend.embed_image_bytes(
            _HEALTH_CHECK_PNG,
            output_dimension=manifest.model.output_dimension,
            instruction=manifest.embedding.instruction,
        )
    except Exception as exc:
        result = library.RuntimeHealthResult(
            runtime_fingerprint=manifest.runtime_fingerprint,
            backend_name="llama.cpp",
            device=manifest.platform.device,
            gpu_name=gpu_name,
            gpu_vendor=gpu_vendor,
            gpu_vendor_id=gpu_vendor_id,
            text_smoke_vector_dim=None,
            image_smoke_vector_dim=None,
            diagnostic_steps=[
                *diagnostic_steps,
                {
                    "step": failure_step,
                    "status": "error",
                    "detail": str(exc),
                },
            ],
            smoke_test_ok=False,
            error=str(exc),
        )
        if library_root is not None:
            _save_last_health_check(library_root, result=result)
        return result
    finally:
        if owned_backend is not None:
            owned_backend.close()

    diagnostic_steps.append(
        {
            "step": "image-embedding-smoke",
            "status": "ok",
            "detail": f"llama.cpp image embedding passed at {int(image_vector.shape[0])}d.",
        }
    )
    result = library.RuntimeHealthResult(
        runtime_fingerprint=manifest.runtime_fingerprint,
        backend_name="llama.cpp",
        device=manifest.platform.device,
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        gpu_vendor_id=gpu_vendor_id,
        text_smoke_vector_dim=int(vector.shape[0]),
        image_smoke_vector_dim=int(image_vector.shape[0]),
        diagnostic_steps=diagnostic_steps,
        smoke_test_ok=True,
        error=None,
    )
    if library_root is not None:
        _save_last_health_check(library_root, result=result)
    return result
