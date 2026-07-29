from __future__ import annotations

import base64
import threading
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
_SESSION_HEALTH_LOCK = threading.Lock()
_SESSION_HEALTH: dict[str, library.RuntimeHealthResult] = {}


class RuntimeAuthorizationError(RuntimeError):
    """The Pinned Runtime did not authorize work in this application session."""


def _library_key(library_root: Path | str) -> str:
    return str(Path(library_root).expanduser().resolve()).casefold()


def _save_last_health_check(
    library_root: Path | str,
    result: library.RuntimeHealthResult,
    record_session_health: bool = True,
) -> None:
    with LibraryStore(library_root) as store:
        store.set_worker_state_json("last_runtime_health_check", result.to_dict())
    if record_session_health:
        with _SESSION_HEALTH_LOCK:
            _SESSION_HEALTH[_library_key(library_root)] = result


def get_current_health_check(
    library_root: Path | str,
) -> library.RuntimeHealthResult | None:
    with _SESSION_HEALTH_LOCK:
        return _SESSION_HEALTH.get(_library_key(library_root))


def runtime_health_matches_manifest(
    health_check: library.RuntimeHealthResult,
) -> bool:
    return health_check.runtime_fingerprint == load_runtime_manifest().runtime_fingerprint


def is_runtime_ready_for_indexing(
    library_root: Path | str,
) -> tuple[bool, str]:
    library.initialize_library(library_root)
    manifest = load_runtime_manifest()
    if not manifest.llama_server_path.is_file():
        return False, "Pinned llama-server is not installed. Run setup."
    if not manifest.main_model_path.is_file():
        return False, "Pinned main GGUF is not installed. Run setup."
    if not manifest.projector_path.is_file():
        return False, "Pinned multimodal projector is not installed. Run setup."

    current_health = get_current_health_check(library_root)
    if current_health is None:
        return False, "Vulkan runtime health has not been checked in this app session."
    if not runtime_health_matches_manifest(current_health):
        return False, "This session's runtime health check is stale for the active manifest."
    if not current_health.smoke_test_ok:
        return False, current_health.error or "Vulkan runtime health check failed."
    return True, "Runtime is ready for indexing."


def _clear_current_health_checks() -> None:
    with _SESSION_HEALTH_LOCK:
        _SESSION_HEALTH.clear()


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
    record_session_health: bool = True,
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
        record_session_health=record_session_health,
        embedding_backend_factory=embedding_backend_factory,
    )


def authorize_runtime_for_session(
    library_root: Path | str,
) -> library.RuntimeHealthResult:
    result = run_runtime_health_check(library_root=library_root)
    if not result.smoke_test_ok:
        raise RuntimeAuthorizationError(
            result.error
            or "Vulkan runtime health check failed; work was not authorized."
        )
    return result


def _create_owned_embedding_backend():
    from .embedding_backend import LlamaCppEmbeddingBackend
    from .inference_service import InferenceScheduler

    return LlamaCppEmbeddingBackend(InferenceScheduler())


def _run_llama_cpp_runtime_health_check(
    manifest,
    library_root: Path | str | None,
    diagnostic_steps: list[dict[str, object]],
    record_session_health: bool = True,
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
            _save_last_health_check(
                library_root,
                result=result,
                record_session_health=record_session_health,
            )
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
            _save_last_health_check(
                library_root,
                result=result,
                record_session_health=record_session_health,
            )
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
        _save_last_health_check(
            library_root,
            result=result,
            record_session_health=record_session_health,
        )
    return result


def get_setup_state(
    library_root: Path | str,
    assets_result: library.AssetListResult | None = None,
    runtime=None,
) -> library.SetupStateResult:
    manifest = load_runtime_manifest()
    if assets_result is None:
        with LibraryStore(library_root) as store:
            assets_result = store.list_asset_summaries()
    last_health_check = get_last_health_check(library_root)
    if runtime is not None:
        current_health_check = runtime.current_health_check()
        runtime_ready, runtime_ready_detail = runtime.is_ready_for_indexing()
    else:
        current_health_check = get_current_health_check(library_root)
        runtime_ready, runtime_ready_detail = is_runtime_ready_for_indexing(library_root)

    assets_present = bool(assets_result.assets)
    indexed_assets_present = any(asset["status"] == "indexed" for asset in assets_result.assets)
    pending_assets_present = any(asset["status"] != "indexed" for asset in assets_result.assets)
    runtime_files_installed = (
        manifest.llama_server_path.is_file()
        and manifest.main_model_path.is_file()
        and manifest.projector_path.is_file()
    )
    health_check_has_run = current_health_check is not None
    health_check_ok = (
        bool(current_health_check.smoke_test_ok)
        and runtime_health_matches_manifest(current_health_check)
        if current_health_check is not None
        else False
    )

    if current_health_check is None:
        health_check_summary = "Vulkan health has not been checked in this app session."
    elif current_health_check.smoke_test_ok:
        health_check_summary = (
            f"This session passed on {current_health_check.device}: "
            f"{current_health_check.gpu_name or 'GPU name unavailable'}."
        )
    else:
        health_check_summary = current_health_check.error or "This session's health check failed."

    if last_health_check is None:
        historical_summary = "No compatible persisted health result is available."
    elif last_health_check.smoke_test_ok:
        historical_summary = (
            f"Last recorded check passed on {last_health_check.device}: "
            f"{last_health_check.gpu_name or 'GPU name unavailable'}."
        )
    else:
        historical_summary = last_health_check.error or "Last recorded health check failed."

    displayed_health = current_health_check or last_health_check

    runtime_readiness = {
        "backend_name": "llama.cpp",
        "device": manifest.platform.device,
        "model_id": manifest.model.id,
        "model_label": manifest.model.base_model_id.split("/")[-1],
        "model_source": str(manifest.model_install_dir),
        "runtime_fingerprint": manifest.runtime_fingerprint,
        "runtime_files_installed": runtime_files_installed,
        "ready": runtime_ready,
        "ready_detail": runtime_ready_detail,
        "current_health_check_ok": health_check_ok,
        "current_health_check_summary": health_check_summary,
        "last_health_check_summary": historical_summary,
        "last_health_text_smoke_vector_dim": (
            displayed_health.text_smoke_vector_dim
            if displayed_health is not None
            else None
        ),
        "last_health_image_smoke_vector_dim": (
            displayed_health.image_smoke_vector_dim
            if displayed_health is not None
            else None
        ),
        "last_health_gpu_name": (
            displayed_health.gpu_name if displayed_health is not None else None
        ),
        "last_health_gpu_vendor": (
            displayed_health.gpu_vendor if displayed_health is not None else None
        ),
        "last_health_gpu_vendor_id": (
            displayed_health.gpu_vendor_id if displayed_health is not None else None
        ),
        "last_health_diagnostic_steps": (
            displayed_health.diagnostic_steps
            if displayed_health is not None
            else []
        ),
    }

    checklist = [
        {
            "id": "runtime-files",
            "label": "Install pinned Vulkan runtime",
            "done": runtime_files_installed,
            "detail": str(manifest.model_install_dir) if runtime_files_installed else "Run setup",
        },
        {
            "id": "health-check",
            "label": "Run runtime health check",
            "done": health_check_ok,
            "detail": health_check_summary,
        },
        {
            "id": "import-assets",
            "label": "Import local assets",
            "done": assets_present,
            "detail": f"{len(assets_result.assets)} assets in the library",
        },
        {
            "id": "indexed-assets",
            "label": "Finish first index build",
            "done": indexed_assets_present,
            "detail": (
                "Searchable assets are available."
                if indexed_assets_present
                else "No indexed assets yet."
            ),
        },
    ]

    return library.SetupStateResult(
        library_root=str(Path(library_root).expanduser().resolve()),
        health_check_has_run=health_check_has_run,
        health_check_ok=health_check_ok,
        health_check_summary=health_check_summary,
        import_source_hint=assets_result.assets[0]["source_records"][0]["source_path"] if assets_result.assets else None,
        assets_present=assets_present,
        indexed_assets_present=indexed_assets_present,
        pending_assets_present=pending_assets_present,
        active_recipe_label=assets_result.active_recipe_label,
        runtime_readiness=runtime_readiness,
        checklist=checklist,
    )
