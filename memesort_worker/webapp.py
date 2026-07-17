from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIServer, make_server

from .app_runtime import WorkerLoopController
from .import_controller import ImportController
from .inference_service import (
    InferenceCancelledError,
    cancel_inference_request,
)
from .app_state import build_app_state
from .asset_browse import get_library_status, list_pending_jobs
from .app_commands import (
    import_and_start_indexing,
    run_jobs_for_active_runtime,
    search_image_for_active_runtime,
    search_text_for_active_runtime,
)
from .library import (
    delete_assets,
    delete_asset,
    delete_pending_jobs,
    get_asset_detail,
    import_folder,
    initialize_library,
    is_runtime_ready_for_indexing,
    list_assets,
    remove_source_record,
    retry_failed_jobs,
    rebuild_active_indexes,
    scan_duplicate_assets,
)
from .native_shell import pick_file, pick_folder, reveal_path_in_file_explorer
from .retrieval_service import find_similar_assets
from .runtime_service import (
    get_setup_state,
    run_runtime_health_check,
)


STATIC_DIR = Path(__file__).with_name("web_static")


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    allow_reuse_address = True
    daemon_threads = True


def authorize_vulkan_for_web_session(library_root: Path) -> None:
    """Run the required same-process Vulkan smoke test before serving work.

    The health result is deliberately process-local: it authorizes the worker
    loop in this application session, rather than trusting a result persisted
    by an earlier run.  Starting the web application is therefore the one
    place that establishes authorization for both normal UI use and the
    desktop launcher's immediate import-and-index action.
    """
    result = run_runtime_health_check(library_root=library_root)
    if not result.smoke_test_ok:
        raise RuntimeError(
            result.error
            or "Vulkan runtime health check failed; the local UI was not started."
        )


def _serve_library_file(library_root: Path, media_path: str) -> tuple[str, list[tuple[str, str]], bytes]:
    candidate = (library_root / media_path).resolve()
    if library_root.resolve() not in candidate.parents and candidate != library_root.resolve():
        return _json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
    if not candidate.exists() or not candidate.is_file():
        return _json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
    body = candidate.read_bytes()
    content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return _text_response(HTTPStatus.OK, body, content_type)


def _json_response(status: HTTPStatus, payload: dict[str, object] | list[object]) -> tuple[str, list[tuple[str, str]], bytes]:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    return f"{status.value} {status.phrase}", headers, body


def _text_response(status: HTTPStatus, body: bytes, content_type: str) -> tuple[str, list[tuple[str, str]], bytes]:
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    return f"{status.value} {status.phrase}", headers, body


def _read_json_body(environ: dict[str, object]) -> dict[str, object]:
    try:
        length = int(str(environ.get("CONTENT_LENGTH") or "0"))
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _serve_static(path: str) -> tuple[str, list[tuple[str, str]], bytes]:
    relative = "index.html" if path in {"", "/"} else path.lstrip("/")
    candidate = (STATIC_DIR / relative).resolve()
    if STATIC_DIR.resolve() not in candidate.parents and candidate != STATIC_DIR.resolve():
        return _json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
    if not candidate.exists() or not candidate.is_file():
        candidate = STATIC_DIR / "index.html"
    body = candidate.read_bytes()
    content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or candidate.suffix in {".js", ".css"}:
        content_type = f"{content_type}; charset=utf-8"
    return _text_response(HTTPStatus.OK, body, content_type)


def _contained_library_path(library_root: Path, relative_path: str) -> Path:
    candidate = (library_root / relative_path).resolve()
    resolved_root = library_root.resolve()
    if resolved_root not in candidate.parents and candidate != resolved_root:
        raise ValueError("Asset path is outside the library root")
    return candidate


def _resolve_asset_reveal_path(library_root: Path, payload: dict[str, object]) -> Path:
    asset_id = str(payload["asset_id"])
    target = str(payload.get("target") or "managed")
    asset = get_asset_detail(library_root, asset_id=asset_id).asset

    if target == "managed":
        return _contained_library_path(library_root, str(asset["library_path"]))

    if target == "source":
        source_path = str(payload.get("source_path") or "")
        source_records = asset.get("source_records") or []
        known_source_paths = {
            str(record.get("source_path"))
            for record in source_records
            if isinstance(record, dict) and record.get("source_path")
        }
        if source_path not in known_source_paths:
            raise ValueError(f"Source record not found for asset {asset_id}: {source_path}")
        return Path(source_path).expanduser().resolve()

    raise ValueError(f"Unknown reveal target: {target}")


def create_app(library_root: str):
    library_root_path = Path(library_root).expanduser().resolve()
    initialize_library(library_root_path)
    worker_loop = WorkerLoopController(library_root_path)
    import_controller = ImportController(library_root_path)

    def app(environ, start_response):
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        raw_path = str(environ.get("PATH_INFO", "/"))
        raw_query_string = str(environ.get("QUERY_STRING", ""))
        parsed = urlparse(f"{raw_path}?{raw_query_string}" if raw_query_string else raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/state" and method == "GET":
                payload = build_app_state(
                    library_root_path,
                    worker_loop_snapshot=worker_loop.snapshot(),
                    import_task_snapshot=import_controller.snapshot().to_dict(),
                ).to_dict()
                status_line, headers, body = _json_response(HTTPStatus.OK, payload)
            elif path == "/api/library-status" and method == "GET":
                result = get_library_status(library_root_path)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/pending-jobs" and method == "GET":
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {"jobs": list_pending_jobs(library_root_path)},
                )
            elif path == "/api/worker-loop" and method == "GET":
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    worker_loop.snapshot().to_dict(),
                )
            elif path == "/api/worker-loop/resume" and method == "POST":
                worker_loop.resume()
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    worker_loop.snapshot().to_dict(),
                )
            elif path == "/api/worker-loop/pause" and method == "POST":
                worker_loop.pause()
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    worker_loop.snapshot().to_dict(),
                )
            elif path == "/api/worker-loop/trigger" and method == "POST":
                worker_loop.trigger_once()
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    worker_loop.snapshot().to_dict(),
                )
            elif path == "/api/health" and method == "POST":
                _read_json_body(environ)
                result = run_runtime_health_check(
                    library_root=library_root_path,
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import-folder" and method == "POST":
                payload = _read_json_body(environ)
                result = import_folder(library_root_path, str(payload["path"]))
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import" and method == "GET":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.snapshot().to_dict())
            elif path == "/api/import/start" and method == "POST":
                payload = _read_json_body(environ)
                start_indexing = bool(payload.get("start_indexing", False))
                if start_indexing:
                    runtime_ready, runtime_message = is_runtime_ready_for_indexing(library_root_path)
                    if not runtime_ready:
                        raise ValueError(runtime_message)
                snapshot = import_controller.start(
                    str(payload["path"]),
                    on_completed=worker_loop.resume if start_indexing else None,
                )
                status_line, headers, body = _json_response(HTTPStatus.ACCEPTED, snapshot.to_dict())
            elif path == "/api/import/pause" and method == "POST":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.pause().to_dict())
            elif path == "/api/import/resume" and method == "POST":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.resume().to_dict())
            elif path == "/api/pick-folder" and method == "POST":
                payload = _read_json_body(environ)
                title = str(payload.get("title") or "Choose a folder")
                initial_path = (
                    str(payload["initial_path"])
                    if payload.get("initial_path")
                    else None
                )
                selected_path = pick_folder(title=title, initial_path=initial_path)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {
                        "selected_path": selected_path,
                    },
                )
            elif path == "/api/pick-file" and method == "POST":
                payload = _read_json_body(environ)
                title = str(payload.get("title") or "Choose a file")
                initial_path = (
                    str(payload["initial_path"])
                    if payload.get("initial_path")
                    else None
                )
                filter_string = str(
                    payload.get("filter_string")
                    or "Image Files|*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp|All Files|*.*"
                )
                selected_path = pick_file(
                    title=title,
                    initial_path=initial_path,
                    filter_string=filter_string,
                )
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {
                        "selected_path": selected_path,
                    },
                )
            elif path == "/api/run-jobs" and method == "POST":
                payload = _read_json_body(environ)
                result = run_jobs_for_active_runtime(
                    library_root_path,
                    max_jobs=int(payload.get("max_jobs", 20)),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import-and-start-index" and method == "POST":
                payload = _read_json_body(environ)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    import_and_start_indexing(
                        library_root_path,
                        str(payload["path"]),
                        worker_loop,
                    ),
                )
            elif path == "/api/assets" and method == "GET":
                result = list_assets(library_root_path)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/asset-detail" and method == "GET":
                asset_id = str(query.get("asset_id", [""])[0])
                result = get_asset_detail(library_root_path, asset_id=asset_id)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/remove-source-record" and method == "POST":
                payload = _read_json_body(environ)
                result = remove_source_record(
                    library_root_path,
                    asset_id=str(payload["asset_id"]),
                    source_path=str(payload["source_path"]),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/delete-asset" and method == "POST":
                payload = _read_json_body(environ)
                result = delete_asset(
                    library_root_path,
                    asset_id=str(payload["asset_id"]),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/assets/batch-action" and method == "POST":
                payload = _read_json_body(environ)
                asset_ids = payload.get("asset_ids")
                if not isinstance(asset_ids, list):
                    raise ValueError("asset_ids must be an array")
                action = str(payload.get("action") or "")
                if action == "delete":
                    result = delete_assets(library_root_path, [str(asset_id) for asset_id in asset_ids])
                elif action == "rebuild-active-index":
                    result = rebuild_active_indexes(library_root_path, [str(asset_id) for asset_id in asset_ids])
                    if result.reindex_jobs_created:
                        worker_loop.resume()
                else:
                    raise ValueError(f"Unsupported batch action: {action}")
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/reveal-asset-file" and method == "POST":
                payload = _read_json_body(environ)
                target_path = _resolve_asset_reveal_path(library_root_path, payload)
                reveal_path_in_file_explorer(target_path)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {
                        "revealed_path": str(target_path),
                        "target": str(payload.get("target") or "managed"),
                    },
                )
            elif path == "/api/retry-failed-jobs" and method == "POST":
                result = retry_failed_jobs(library_root_path)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/pending-jobs/delete" and method == "POST":
                payload = _read_json_body(environ)
                job_ids = payload.get("job_ids")
                if not isinstance(job_ids, list):
                    raise ValueError("job_ids must be an array")
                result = delete_pending_jobs(library_root_path, [str(job_id) for job_id in job_ids])
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search" and method == "GET":
                query_text = str(query.get("query", [""])[0])
                top_k = int(query.get("top_k", ["12"])[0])
                request_id = str(query.get("request_id", [""])[0])
                result = search_text_for_active_runtime(
                    library_root_path,
                    query=query_text,
                    top_k=top_k,
                    request_id=request_id,
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search-image" and method == "POST":
                payload = _read_json_body(environ)
                result = search_image_for_active_runtime(
                    library_root_path,
                    image_path=str(payload["path"]),
                    top_k=int(payload.get("top_k", 18)),
                    request_id=str(payload.get("request_id") or ""),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search/cancel" and method == "POST":
                payload = _read_json_body(environ)
                request_id = str(payload.get("request_id") or "")
                was_active = cancel_inference_request(request_id)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {
                        "request_id": request_id,
                        "cancelled": True,
                        "was_active": was_active,
                    },
                )
            elif path == "/api/find-similar" and method == "GET":
                asset_id = str(query.get("asset_id", [""])[0])
                top_k = int(query.get("top_k", ["12"])[0])
                result = find_similar_assets(library_root_path, asset_id=asset_id, top_k=top_k)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/duplicates" and method == "GET":
                threshold = float(query.get("threshold", ["0.92"])[0])
                result = scan_duplicate_assets(library_root_path, threshold=threshold)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path.startswith("/media/") and method == "GET":
                media_path = path.removeprefix("/media/")
                status_line, headers, body = _serve_library_file(library_root_path, media_path)
            elif path.startswith("/api/"):
                status_line, headers, body = _json_response(
                    HTTPStatus.NOT_FOUND,
                    {"error": "NotFound", "detail": f"Unknown API endpoint: {path}"},
                )
            else:
                status_line, headers, body = _serve_static(path)
        except InferenceCancelledError as exc:
            status_line, headers, body = _json_response(
                HTTPStatus.CONFLICT,
                {"error": type(exc).__name__, "detail": str(exc)},
            )
        except Exception as exc:
            status_line, headers, body = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": type(exc).__name__, "detail": str(exc)},
            )

        start_response(status_line, headers)
        return [body]

    def shutdown() -> None:
        import_controller.shutdown()
        worker_loop.shutdown()

    app.shutdown = shutdown  # type: ignore[attr-defined]
    return app


def run_web_app(
    library_root: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    on_started=None,
) -> None:
    app = create_app(library_root)
    try:
        authorize_vulkan_for_web_session(Path(library_root).expanduser().resolve())
        with make_server(host, port, app, server_class=ThreadedWSGIServer) as server:
            socket_host, socket_port = server.socket.getsockname()[:2]
            payload = {
                "host": socket_host,
                "port": socket_port,
                "url": f"http://{socket_host}:{socket_port}/",
                "library_root": str(Path(library_root).resolve()),
            }
            print(json.dumps(payload))
            if on_started is not None:
                on_started(payload)
            server.serve_forever()
    finally:
        app.shutdown()
