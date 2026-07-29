from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .app_runtime import WorkerLoopController
from .import_controller import ImportController
from .inference_service import (
    InferenceCancelledError,
    cancel_inference_request,
)
from .app_state import build_app_state
from .app_commands import (
    import_and_start_indexing,
    rebuild_assets_and_resume,
    resolve_asset_reveal_path,
    start_background_import,
)
from .asset_catalog import (
    delete_asset,
    delete_assets,
    delete_pending_jobs,
    import_folder,
    initialize_library,
    remove_source_record,
    retry_failed_jobs,
)
from .indexing_pipeline import run_pending_jobs
from .library_store import LibraryStore
from .native_shell import pick_file, pick_folder, reveal_path_in_file_explorer
from .retrieval_service import find_similar_assets, search_image_path, search_text
from .runtime_service import (
    authorize_runtime_for_session,
    run_runtime_health_check,
)
from .web_security import (
    SECURITY_HEADERS,
    GateOutcome,
    SessionGate,
    request_headers_from_environ,
)


STATIC_DIR = Path(__file__).with_name("web_static")

DEFAULT_MAX_REQUEST_BODY_BYTES = 32 * 1024 * 1024


class RequestBodyTooLargeError(ValueError):
    """The request body exceeded the configured size limit."""


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    allow_reuse_address = True
    daemon_threads = True


class QuietWSGIRequestHandler(WSGIRequestHandler):
    """Suppress default access logging.

    The bootstrap URL carries the one-time session secret in its query string,
    so the standard request-line log would write the secret (and library paths)
    to stderr. Silencing it keeps secrets and paths out of logs.
    """

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return


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


def _redirect_response(location: str) -> tuple[str, list[tuple[str, str]], bytes]:
    status = HTTPStatus.SEE_OTHER
    headers = [
        ("Location", location),
        ("Content-Length", "0"),
        ("Cache-Control", "no-store"),
    ]
    return f"{status.value} {status.phrase}", headers, b""


def _read_json_body(environ: dict[str, object], max_bytes: int) -> dict[str, object]:
    try:
        length = int(str(environ.get("CONTENT_LENGTH") or "0"))
    except ValueError:
        length = 0
    if length < 0:
        raise ValueError("Negative request body length")
    if length > max_bytes:
        raise RequestBodyTooLargeError(
            f"Request body of {length} bytes exceeds the {max_bytes} byte limit"
        )
    raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _serve_static(static_dir: Path, path: str) -> tuple[str, list[tuple[str, str]], bytes]:
    relative = "index.html" if path in {"", "/"} else path.lstrip("/")
    candidate = (static_dir / relative).resolve()
    if static_dir.resolve() not in candidate.parents and candidate != static_dir.resolve():
        return _json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
    if not candidate.exists() or not candidate.is_file():
        candidate = static_dir / "index.html"
    body = candidate.read_bytes()
    content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    if content_type.startswith("text/") or candidate.suffix in {".js", ".css"}:
        content_type = f"{content_type}; charset=utf-8"
    return _text_response(HTTPStatus.OK, body, content_type)


def _apply_gate_outcome(
    outcome: GateOutcome,
    extra_headers: list[tuple[str, str]],
) -> tuple[str, list[tuple[str, str]], bytes] | None:
    """Translate a gate verdict into a short-circuit response, or allow the request.

    Returning ``None`` means the request is authenticated and may proceed.
    """
    if outcome.action == "allow":
        return None
    if outcome.action == "bootstrap":
        if outcome.set_cookie is not None:
            extra_headers.append(("Set-Cookie", outcome.set_cookie))
        return _redirect_response(outcome.location or "/")
    return _json_response(
        outcome.status,
        {"error": "Forbidden", "detail": outcome.detail},
    )


def _finalize_headers(
    headers: list[tuple[str, str]],
    extra_headers: list[tuple[str, str]],
    security: SessionGate | None,
) -> list[tuple[str, str]]:
    merged = list(headers)
    merged.extend(extra_headers)
    if security is not None:
        merged.extend(SECURITY_HEADERS)
    return merged


class LocalWebApp:
    """A callable WSGI application with an explicit lifecycle.

    The window layer never touches the worker threads directly. It calls
    ``begin_shutdown`` to stop accepting mutations, then ``shutdown`` to release
    the import controller and worker loop.
    """

    def __init__(
        self,
        *,
        handler,
        worker_loop: WorkerLoopController,
        import_controller: ImportController,
        stopping: threading.Event,
    ) -> None:
        self._handler = handler
        self._worker_loop = worker_loop
        self._import_controller = import_controller
        self._stopping = stopping

    def __call__(self, environ, start_response):
        return self._handler(environ, start_response)

    def begin_shutdown(self) -> None:
        self._stopping.set()

    def shutdown(self) -> None:
        self._stopping.set()
        self._import_controller.shutdown()
        self._worker_loop.shutdown()


def create_app(
    library_root: str,
    *,
    security: SessionGate | None = None,
    static_root: Path | None = None,
    max_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
) -> "LocalWebApp":
    library_root_path = Path(library_root).expanduser().resolve()
    initialize_library(library_root_path)
    worker_loop = WorkerLoopController(library_root_path)
    import_controller = ImportController(library_root_path)
    static_dir = static_root or STATIC_DIR
    stopping = threading.Event()

    def app(environ, start_response):
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        raw_path = str(environ.get("PATH_INFO", "/"))
        raw_query_string = str(environ.get("QUERY_STRING", ""))
        parsed = urlparse(f"{raw_path}?{raw_query_string}" if raw_query_string else raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)

        extra_headers: list[tuple[str, str]] = []
        if security is not None:
            outcome = security.evaluate(
                method,
                query,
                request_headers_from_environ(environ),
            )
            gate_response = _apply_gate_outcome(outcome, extra_headers)
            if gate_response is not None:
                status_line, headers, body = gate_response
                start_response(status_line, _finalize_headers(headers, extra_headers, security))
                return [body]

        if stopping.is_set() and method not in ("GET", "HEAD"):
            status_line, headers, body = _json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "ShuttingDown", "detail": "The application is shutting down."},
            )
            start_response(status_line, _finalize_headers(headers, extra_headers, security))
            return [body]

        try:
            if path == "/api/state" and method == "GET":
                payload = build_app_state(
                    library_root_path,
                    worker_loop_snapshot=worker_loop.snapshot(),
                    import_task_snapshot=import_controller.snapshot().to_dict(),
                ).to_dict()
                status_line, headers, body = _json_response(HTTPStatus.OK, payload)
            elif path == "/api/library-status" and method == "GET":
                with LibraryStore(library_root_path) as store:
                    result = store.get_library_status()
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/pending-jobs" and method == "GET":
                with LibraryStore(library_root_path) as store:
                    jobs = store.list_pending_jobs()
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {"jobs": jobs},
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
                _read_json_body(environ, max_body_bytes)
                result = run_runtime_health_check(
                    library_root=library_root_path,
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import-folder" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                result = import_folder(library_root_path, str(payload["path"]))
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import" and method == "GET":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.snapshot().to_dict())
            elif path == "/api/import/start" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                snapshot = start_background_import(
                    library_root_path,
                    str(payload["path"]),
                    import_controller,
                    worker_loop,
                    start_indexing=bool(payload.get("start_indexing", False)),
                )
                status_line, headers, body = _json_response(HTTPStatus.ACCEPTED, snapshot.to_dict())
            elif path == "/api/import/pause" and method == "POST":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.pause().to_dict())
            elif path == "/api/import/resume" and method == "POST":
                status_line, headers, body = _json_response(HTTPStatus.OK, import_controller.resume().to_dict())
            elif path == "/api/pick-folder" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
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
                payload = _read_json_body(environ, max_body_bytes)
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
                payload = _read_json_body(environ, max_body_bytes)
                result = run_pending_jobs(
                    library_root_path,
                    max_jobs=int(payload.get("max_jobs", 20)),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/import-and-start-index" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    import_and_start_indexing(
                        library_root_path,
                        str(payload["path"]),
                        worker_loop,
                    ),
                )
            elif path == "/api/assets" and method == "GET":
                with LibraryStore(library_root_path) as store:
                    result = store.list_assets_detailed()
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/asset-detail" and method == "GET":
                asset_id = str(query.get("asset_id", [""])[0])
                with LibraryStore(library_root_path) as store:
                    result = store.get_asset_detail(asset_id)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/remove-source-record" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                result = remove_source_record(
                    library_root_path,
                    asset_id=str(payload["asset_id"]),
                    source_path=str(payload["source_path"]),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/delete-asset" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                result = delete_asset(
                    library_root_path,
                    asset_id=str(payload["asset_id"]),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/assets/batch-action" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                asset_ids = payload.get("asset_ids")
                if not isinstance(asset_ids, list):
                    raise ValueError("asset_ids must be an array")
                action = str(payload.get("action") or "")
                if action == "delete":
                    result = delete_assets(library_root_path, [str(asset_id) for asset_id in asset_ids])
                elif action == "rebuild-active-index":
                    result = rebuild_assets_and_resume(
                        library_root_path,
                        [str(asset_id) for asset_id in asset_ids],
                        worker_loop,
                    )
                else:
                    raise ValueError(f"Unsupported batch action: {action}")
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/reveal-asset-file" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                target = str(payload.get("target") or "managed")
                target_path = resolve_asset_reveal_path(
                    library_root_path,
                    asset_id=str(payload["asset_id"]),
                    target=target,
                    source_path=str(payload.get("source_path") or ""),
                )
                reveal_path_in_file_explorer(target_path)
                status_line, headers, body = _json_response(
                    HTTPStatus.OK,
                    {
                        "revealed_path": str(target_path),
                        "target": target,
                    },
                )
            elif path == "/api/retry-failed-jobs" and method == "POST":
                result = retry_failed_jobs(library_root_path)
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/pending-jobs/delete" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                job_ids = payload.get("job_ids")
                if not isinstance(job_ids, list):
                    raise ValueError("job_ids must be an array")
                result = delete_pending_jobs(library_root_path, [str(job_id) for job_id in job_ids])
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search" and method == "GET":
                query_text = str(query.get("query", [""])[0])
                top_k = int(query.get("top_k", ["12"])[0])
                request_id = str(query.get("request_id", [""])[0])
                result = search_text(
                    library_root_path,
                    query=query_text,
                    top_k=top_k,
                    request_id=request_id,
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search-image" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
                result = search_image_path(
                    library_root_path,
                    image_path=str(payload["path"]),
                    top_k=int(payload.get("top_k", 18)),
                    request_id=str(payload.get("request_id") or ""),
                )
                status_line, headers, body = _json_response(HTTPStatus.OK, result.to_dict())
            elif path == "/api/search/cancel" and method == "POST":
                payload = _read_json_body(environ, max_body_bytes)
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
                with LibraryStore(library_root_path) as store:
                    result = store.scan_duplicate_assets(threshold)
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
                status_line, headers, body = _serve_static(static_dir, path)
        except InferenceCancelledError as exc:
            status_line, headers, body = _json_response(
                HTTPStatus.CONFLICT,
                {"error": type(exc).__name__, "detail": str(exc)},
            )
        except RequestBodyTooLargeError as exc:
            status_line, headers, body = _json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": type(exc).__name__, "detail": str(exc)},
            )
        except Exception as exc:
            status_line, headers, body = _json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": type(exc).__name__, "detail": str(exc)},
            )

        start_response(status_line, _finalize_headers(headers, extra_headers, security))
        return [body]

    return LocalWebApp(
        handler=app,
        worker_loop=worker_loop,
        import_controller=import_controller,
        stopping=stopping,
    )


def run_web_app(
    library_root: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    on_started=None,
) -> None:
    app = create_app(library_root)
    try:
        authorize_runtime_for_session(Path(library_root).expanduser().resolve())
        with make_server(
            host,
            port,
            app,
            server_class=ThreadedWSGIServer,
            handler_class=QuietWSGIRequestHandler,
        ) as server:
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
