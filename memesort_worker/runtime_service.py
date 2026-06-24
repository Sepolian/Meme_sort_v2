from __future__ import annotations

from pathlib import Path

import memesort_worker.library as library
from .library_store import LibraryStore


def _persist_resolved_model_source(
    library_root: Path | str,
    resolved_model_source: str | None,
) -> library.RuntimeSettings:
    settings = library.get_runtime_settings(library_root)
    if not resolved_model_source:
        return settings
    if settings.model_name_or_path and library._is_local_model_path(settings.model_name_or_path):
        return settings
    if settings.model_name_or_path == resolved_model_source:
        return settings
    return library.save_runtime_settings(
        library_root,
        selected_profile=settings.selected_profile,
        selected_model_key=settings.selected_model_key,
        model_name_or_path=resolved_model_source,
        selected_recipe_preset=settings.selected_recipe_preset,
        gif_frame_count=settings.gif_frame_count,
        backend_name=settings.backend_name,
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


def _resolve_runtime_model_source_for_backend(
    backend_name: str,
    model_name_or_path: str | None,
    selected_model_key: str | None = None,
    allow_download: bool = False,
) -> str | None:
    if backend_name != "qwen3-vl":
        return model_name_or_path

    if selected_model_key is not None:
        return library.resolve_effective_model_source(
            selected_model_key,
            model_name_or_path,
            allow_download=allow_download,
        )

    configured_source = library._configured_model_source(model_name_or_path)
    if configured_source and not library._is_local_model_path(configured_source) and allow_download:
        return library.ensure_project_local_model_snapshot(configured_source)
    return configured_source


def _infer_model_source_origin(
    requested_model_name_or_path: str | None,
    resolved_model_source: str | None,
    selected_model_key: str | None,
) -> str | None:
    if not resolved_model_source:
        return None

    if requested_model_name_or_path and library._is_local_model_path(requested_model_name_or_path):
        return "explicit-local-path"

    resolved_path = Path(resolved_model_source).expanduser().resolve()
    project_store_root = library.project_model_store_root().resolve()
    if resolved_path == project_store_root or project_store_root in resolved_path.parents:
        return "project-local-model-store"

    if selected_model_key is not None:
        discovered = library.discover_local_model_path(selected_model_key)
        if discovered and str(Path(discovered).expanduser().resolve()) == str(resolved_path):
            return "discovered-local-snapshot"

    return "configured-model-source"


def run_runtime_health_check(
    profile_id: str,
    model_key: str = "qwen3-2b",
    model_name_or_path: str | None = None,
    library_root: Path | str | None = None,
) -> library.RuntimeHealthResult:
    profile = library.get_runtime_profile(profile_id)
    model_variant = library.get_model_variant(model_key)
    resolved_model_source = model_name_or_path
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

    try:
        import torch  # type: ignore
    except ImportError:
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="qwen3-vl",
            model_name_or_path=model_name_or_path,
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
                    "step": "torch-import",
                    "status": "error",
                    "detail": "PyTorch is not installed in the active virtual environment.",
                },
            ],
            smoke_test_ok=False,
            error="PyTorch is not installed in the active virtual environment.",
        )
        if library_root is not None:
            _save_last_health_check(library_root, result=result)
        return result

    diagnostic_steps.append(
        {
            "step": "torch-import",
            "status": "ok",
            "detail": "PyTorch import succeeded.",
        }
    )

    cuda_available = bool(torch.cuda.is_available())
    gpu_name = None
    if cuda_available:
        try:
            gpu_name = str(torch.cuda.get_device_name(0))
        except Exception:
            gpu_name = "unknown-cuda-device"

    diagnostic_steps.append(
        {
            "step": "cuda-availability",
            "status": "ok" if (not profile.device.startswith("cuda") or cuda_available) else "error",
            "detail": (
                f"CUDA visible as {gpu_name or 'unknown-cuda-device'}."
                if cuda_available
                else "CUDA is not available to PyTorch."
            ),
        }
    )

    if profile.device.startswith("cuda") and not cuda_available:
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="qwen3-vl",
            model_name_or_path=model_name_or_path,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=True,
            cuda_available=False,
            gpu_name=gpu_name,
            model_source_origin=None,
            model_downloaded=False,
            text_smoke_vector_dim=None,
            diagnostic_steps=diagnostic_steps,
            smoke_test_ok=False,
            error="CUDA profile selected but torch.cuda.is_available() is false.",
        )
        if library_root is not None:
            _save_last_health_check(library_root, result=result)
        return result

    prior_local_source = library.resolve_effective_model_source(
        model_key,
        model_name_or_path,
        allow_download=False,
    )
    try:
        resolved_model_source = _resolve_runtime_model_source_for_backend(
            "qwen3-vl",
            model_name_or_path,
            selected_model_key=model_key,
            allow_download=True,
        )
    except Exception as exc:
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="qwen3-vl",
            model_name_or_path=model_name_or_path,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=True,
            cuda_available=cuda_available,
            gpu_name=gpu_name,
            model_source_origin=None,
            model_downloaded=False,
            text_smoke_vector_dim=None,
            diagnostic_steps=[
                *diagnostic_steps,
                {
                    "step": "resolve-model-source",
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

    model_source_origin = _infer_model_source_origin(
        model_name_or_path,
        resolved_model_source,
        model_key,
    )
    model_downloaded = bool(resolved_model_source and not prior_local_source)
    diagnostic_steps.append(
        {
            "step": "resolve-model-source",
            "status": "ok" if resolved_model_source else "error",
            "detail": resolved_model_source or "No model source is ready yet.",
        }
    )

    if not resolved_model_source:
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="qwen3-vl",
            model_name_or_path=None,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=True,
            cuda_available=cuda_available,
            gpu_name=gpu_name,
            model_source_origin=None,
            model_downloaded=False,
            text_smoke_vector_dim=None,
            diagnostic_steps=diagnostic_steps,
            smoke_test_ok=False,
            error="Model source is not ready yet.",
        )
        if library_root is not None:
            _save_last_health_check(library_root, result=result)
        return result

    try:
        backend = library.get_embedding_backend(
            "qwen3-vl",
            library.get_runtime_config_for_profile(
                profile.profile_id,
                model_name_or_path=resolved_model_source,
            ),
        )
        vector = backend.embed_text(
            "confused reaction image",
            output_dimension=model_variant.output_dimension,
            instruction=library.INSTRUCTION_TEXT_BY_KEY["qwen3vl-text-to-image-default-v1"],
        )
    except Exception as exc:
        result = library.RuntimeHealthResult(
            profile_id=profile.profile_id,
            backend_name="qwen3-vl",
            model_name_or_path=resolved_model_source,
            selected_model_key=model_variant.model_key,
            selected_model_label=model_variant.label,
            device=profile.device,
            torch_dtype=profile.torch_dtype,
            torch_available=True,
            cuda_available=cuda_available,
            gpu_name=gpu_name,
            model_source_origin=model_source_origin,
            model_downloaded=model_downloaded,
            text_smoke_vector_dim=None,
            diagnostic_steps=[
                *diagnostic_steps,
                {
                    "step": "text-embedding-smoke",
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
            "step": "text-embedding-smoke",
            "status": "ok",
            "detail": f"Text embedding smoke passed at {int(vector.shape[0])}d.",
        }
    )

    result = library.RuntimeHealthResult(
        profile_id=profile.profile_id,
        backend_name="qwen3-vl",
        model_name_or_path=resolved_model_source,
        selected_model_key=model_variant.model_key,
        selected_model_label=model_variant.label,
        device=profile.device,
        torch_dtype=profile.torch_dtype,
        torch_available=True,
        cuda_available=cuda_available,
        gpu_name=gpu_name,
        model_source_origin=model_source_origin,
        model_downloaded=model_downloaded,
        text_smoke_vector_dim=int(vector.shape[0]),
        diagnostic_steps=diagnostic_steps,
        smoke_test_ok=True,
        error=None,
    )
    if library_root is not None:
        _save_last_health_check(library_root, result=result)
    return result


def apply_runtime_selection(
    library_root: Path | str,
    selected_profile: str,
    selected_model_key: str,
    model_name_or_path: str | None,
    gif_frame_count: int | None = None,
    backend_name: str = "qwen3-vl",
) -> library.ApplyRuntimeSelectionResult:
    settings = library.save_runtime_settings(
        library_root,
        selected_profile=selected_profile,
        selected_model_key=selected_model_key,
        model_name_or_path=model_name_or_path,
        selected_recipe_preset=library.resolve_recipe_preset(selected_profile, selected_model_key),
        gif_frame_count=gif_frame_count,
        backend_name=backend_name,
    )
    switch_result = library.switch_active_recipe(
        library_root,
        preset_key=settings.selected_recipe_preset,
        gif_frame_count=settings.gif_frame_count,
    )
    return library.ApplyRuntimeSelectionResult(
        runtime_settings=settings.to_dict(),
        active_recipe_id=switch_result.active_recipe_id,
        active_recipe_label=switch_result.active_recipe_label,
        reindex_jobs_created=switch_result.reindex_jobs_created,
        assets_seen=switch_result.assets_seen,
    )


def run_first_run_flow(
    library_root: Path | str,
    selected_profile: str,
    selected_model_key: str,
    model_name_or_path: str | None,
    import_path: str | None = None,
    gif_frame_count: int | None = None,
    backend_name: str = "qwen3-vl",
) -> library.FirstRunFlowResult:
    runtime_selection = apply_runtime_selection(
        library_root,
        selected_profile=selected_profile,
        selected_model_key=selected_model_key,
        model_name_or_path=model_name_or_path,
        gif_frame_count=gif_frame_count,
        backend_name=backend_name,
    )
    health_check = run_runtime_health_check(
        selected_profile,
        model_key=selected_model_key,
        model_name_or_path=model_name_or_path,
        library_root=library_root,
    )
    _persist_resolved_model_source(library_root, health_check.model_name_or_path)

    should_resume_worker_loop = False
    import_result: dict[str, object] | None = None
    if health_check.smoke_test_ok and import_path:
        import_result = library.import_folder(library_root, import_path).to_dict()
        should_resume_worker_loop = True

    setup_state = get_setup_state(library_root).to_dict()
    if not health_check.smoke_test_ok:
        next_step = "Fix the runtime issue in health check and run the guided flow again."
    elif not import_path:
        next_step = "Runtime is ready. Choose a folder to import and run the guided flow again."
    elif import_result and int(import_result.get("jobs_created", 0)) > 0:
        next_step = "Runtime is ready, assets are imported, and background indexing can continue now."
    else:
        next_step = "Runtime is ready. Imported files are already known, so you can go straight to search or duplicate review."

    return library.FirstRunFlowResult(
        runtime_selection=runtime_selection.to_dict(),
        health_check=health_check.to_dict(),
        import_result=import_result,
        setup_state=setup_state,
        should_resume_worker_loop=should_resume_worker_loop,
        next_step=next_step,
    )


def get_setup_state(library_root: Path | str) -> library.SetupStateResult:
    settings = library.get_runtime_settings(library_root)
    assets_result = library.list_assets(library_root)
    last_health_check = get_last_health_check(library_root)
    suggested_model_path = library.discover_local_model_path(settings.selected_model_key)
    effective_model_source = library.resolve_effective_model_source(
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
    health_check_ok = bool(last_health_check.smoke_test_ok) if last_health_check is not None else False

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
