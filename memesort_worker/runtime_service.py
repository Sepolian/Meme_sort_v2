from __future__ import annotations

import base64
from pathlib import Path

from .asset_browse import list_asset_summaries
from . import library_internal as library
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


def _save_last_health_check(
    library_root: Path | str,
    result: library.RuntimeHealthResult,
) -> None:
    with LibraryStore(library_root) as store:
        store.set_worker_state_json("last_runtime_health_check", result.to_dict())


def get_last_health_check(
    library_root: Path | str,
) -> library.RuntimeHealthResult | None:
    with LibraryStore(library_root) as store:
        payload = store.get_worker_state_json("last_runtime_health_check")
        if payload is None:
            return None
        return library.RuntimeHealthResult(
            profile_id=str(payload["profile_id"]),
            backend_name=str(payload["backend_name"]),
            model_name_or_path=(
                str(payload["model_name_or_path"])
                if payload.get("model_name_or_path")
                else None
            ),
            selected_model_key=(
                str(payload["selected_model_key"])
                if payload.get("selected_model_key")
                else None
            ),
            selected_model_label=(
                str(payload["selected_model_label"])
                if payload.get("selected_model_label")
                else None
            ),
            device=str(payload["device"]),
            torch_dtype=str(payload["torch_dtype"]),
            torch_available=bool(payload["torch_available"]),
            cuda_available=bool(payload["cuda_available"]),
            gpu_name=str(payload["gpu_name"]) if payload.get("gpu_name") else None,
            model_source_origin=(
                str(payload["model_source_origin"])
                if payload.get("model_source_origin")
                else None
            ),
            model_downloaded=bool(payload.get("model_downloaded", False)),
            text_smoke_vector_dim=(
                int(payload["text_smoke_vector_dim"])
                if payload.get("text_smoke_vector_dim") is not None
                else None
            ),
            diagnostic_steps=list(payload.get("diagnostic_steps", [])),
            smoke_test_ok=bool(payload["smoke_test_ok"]),
            error=str(payload["error"]) if payload.get("error") else None,
        )


def run_runtime_health_check(
    profile_id: str,
    model_key: str = library.MANIFEST_MODEL_KEY,
    model_name_or_path: str | None = None,
    library_root: Path | str | None = None,
) -> library.RuntimeHealthResult:
    if model_name_or_path is not None:
        raise ValueError("Vulkan model path is owned by runtime-manifest.json")
    profile = library.get_runtime_profile(profile_id)
    model_variant = library.get_model_variant(model_key)
    library.resolve_recipe_preset(profile.profile_id, model_variant.model_key)
    diagnostic_steps: list[dict[str, object]] = [
        {
            "step": "runtime-profile",
            "status": "ok",
            "detail": profile.profile_id,
        },
        {
            "step": "embedding-model",
            "status": "ok",
            "detail": model_variant.model_id,
        },
    ]

    return _run_llama_cpp_runtime_health_check(
        profile=profile,
        model_variant=model_variant,
        library_root=library_root,
        diagnostic_steps=diagnostic_steps,
    )


def _run_llama_cpp_runtime_health_check(
    profile,
    model_variant,
    library_root: Path | str | None,
    diagnostic_steps: list[dict[str, object]],
) -> library.RuntimeHealthResult:
    manifest = load_runtime_manifest()
    resolved_model_source = str(manifest.model_install_dir)
    if not manifest.main_model_path.is_file() or not manifest.projector_path.is_file():
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="llama.cpp",
            model_name_or_path=resolved_model_source,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=False,
            cuda_available=False,
            gpu_name=None,
            model_source_origin=None,
            model_downloaded=False,
            text_smoke_vector_dim=None,
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
        backend = library.get_embedding_backend()
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
            profile_id=profile.profile_id,
            backend_name="llama.cpp",
            model_name_or_path=resolved_model_source,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=False,
            cuda_available=False,
            gpu_name=gpu_name,
            model_source_origin="project-local-model-store",
            model_downloaded=False,
            text_smoke_vector_dim=None,
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

    diagnostic_steps.append(
        {
            "step": "image-embedding-smoke",
            "status": "ok",
            "detail": f"llama.cpp image embedding passed at {int(image_vector.shape[0])}d.",
        }
    )
    result = library.RuntimeHealthResult(
        profile_id=profile.profile_id,
        backend_name="llama.cpp",
        model_name_or_path=resolved_model_source,
        selected_model_key=model_variant.model_key,
        selected_model_label=model_variant.label,
        device=profile.device,
        torch_dtype=profile.torch_dtype,
        torch_available=False,
        cuda_available=False,
        gpu_name=gpu_name,
        model_source_origin="project-local-model-store",
        model_downloaded=False,
        text_smoke_vector_dim=int(vector.shape[0]),
        diagnostic_steps=diagnostic_steps,
        smoke_test_ok=True,
        error=None,
    )
    if library_root is not None:
        _save_last_health_check(library_root, result=result)
    return result


def get_setup_state(library_root: Path | str) -> library.SetupStateResult:
    settings = library.get_runtime_settings(library_root)
    assets_result = list_asset_summaries(library_root)
    last_health_check = get_last_health_check(library_root)
    suggested_model_path = library.discover_local_gguf_model_path(
        settings.selected_model_key
    )
    effective_model_source = library.resolve_effective_model_source_for_backend(
        settings.backend_name,
        settings.selected_model_key,
        settings.model_name_or_path,
    )
    runtime_ready, runtime_ready_detail = library.is_runtime_ready_for_indexing(library_root)

    assets_present = bool(assets_result.assets)
    indexed_assets_present = any(asset["status"] == "indexed" for asset in assets_result.assets)
    pending_assets_present = any(asset["status"] != "indexed" for asset in assets_result.assets)
    runtime_profile_selected = bool(settings.selected_profile)
    embedding_model_selected = bool(settings.selected_model_key)
    model_path_configured = bool(effective_model_source)
    runtime_backend_selected = bool(settings.backend_name)
    health_check_has_run = last_health_check is not None
    health_check_ok = (
        bool(last_health_check.smoke_test_ok)
        and library.runtime_health_matches_settings(settings, last_health_check)
        if last_health_check is not None
        else False
    )

    if last_health_check is None:
        health_check_summary = "Health check has not been run yet."
    elif last_health_check.smoke_test_ok:
        health_check_summary = (
            f"Last health check passed for {last_health_check.profile_id} on "
            f"{last_health_check.device}."
        )
    else:
        health_check_summary = last_health_check.error or "Last health check failed."

    runtime_readiness = {
        "backend_name": settings.backend_name,
        "selected_profile": settings.selected_profile,
        "selected_model_key": settings.selected_model_key,
        "selected_model_label": library.get_model_variant(settings.selected_model_key).label,
        "suggested_model_path": suggested_model_path,
        "recommended_model_source": effective_model_source,
        "ready": runtime_ready,
        "ready_detail": runtime_ready_detail,
        "last_health_check_ok": health_check_ok if last_health_check is not None else False,
        "last_health_check_summary": (
            health_check_summary
            if last_health_check is not None
            else "Health check has not been run yet."
        ),
        "last_health_model_source_origin": (
            last_health_check.model_source_origin
            if last_health_check is not None
            else None
        ),
        "last_health_model_downloaded": (
            last_health_check.model_downloaded
            if last_health_check is not None
            else False
        ),
        "last_health_text_smoke_vector_dim": (
            last_health_check.text_smoke_vector_dim
            if last_health_check is not None
            else None
        ),
        "last_health_gpu_name": (
            last_health_check.gpu_name
            if last_health_check is not None
            else None
        ),
        "last_health_diagnostic_steps": (
            last_health_check.diagnostic_steps
            if last_health_check is not None
            else []
        ),
    }

    checklist = [
        {
            "id": "runtime-profile",
            "label": "Choose runtime profile",
            "done": runtime_profile_selected,
            "detail": settings.selected_profile,
        },
        {
            "id": "embedding-model",
            "label": "Choose embedding model",
            "done": embedding_model_selected,
            "detail": settings.selected_model_key,
        },
        {
            "id": "model-path",
            "label": "Resolve model source",
            "done": model_path_configured,
            "detail": (
                effective_model_source
                or suggested_model_path
                or "Not ready"
            ),
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
        runtime_profile_selected=runtime_profile_selected,
        embedding_model_selected=embedding_model_selected,
        model_path_configured=model_path_configured,
        runtime_backend_selected=runtime_backend_selected,
        health_check_has_run=health_check_has_run,
        health_check_ok=health_check_ok,
        health_check_summary=health_check_summary,
        import_source_hint=assets_result.assets[0]["source_records"][0]["source_path"] if assets_result.assets else None,
        assets_present=assets_present,
        indexed_assets_present=indexed_assets_present,
        pending_assets_present=pending_assets_present,
        active_recipe_label=assets_result.active_recipe_label,
        suggested_model_path=suggested_model_path,
        runtime_readiness=runtime_readiness,
        checklist=checklist,
    )
