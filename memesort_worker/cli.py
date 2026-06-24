from __future__ import annotations

import argparse
import json
from typing import Sequence

from .desktop_app import launch_desktop_shell
from .launcher import launch_local_mvp_app
from .library import (
    import_folder,
    initialize_library,
    list_assets,
    run_pending_jobs,
    switch_active_recipe,
)
from .retrieval_service import find_similar_assets, search_image_path, search_text
from .webapp import run_web_app


BACKEND_CHOICES = ("debug", "qwen3-vl")


def _add_embedding_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        default="debug",
        choices=BACKEND_CHOICES,
        help="Embedding backend",
    )
    parser.add_argument(
        "--model-name-or-path",
        default=None,
        help="Required for qwen3-vl. Hugging Face model id or local model path.",
    )
    parser.add_argument(
        "--torch-dtype",
        default="auto",
        help="Torch dtype for qwen3-vl, for example auto, float16, bfloat16, float32.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Optional torch device for qwen3-vl, for example cpu or cuda:0.",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=None,
        help="Optional torch intra-op thread count for qwen3-vl.",
    )
    parser.add_argument(
        "--num-interop-threads",
        type=int,
        default=None,
        help="Optional torch inter-op thread count for qwen3-vl.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memesort-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-library", help="Create library directories and database")
    init_parser.add_argument("--root", required=True, help="Library root directory")

    import_parser = subparsers.add_parser("import-folder", help="Import files into the library")
    import_parser.add_argument("--library-root", required=True, help="Library root directory")
    import_parser.add_argument("--path", required=True, help="Source folder to import")

    list_parser = subparsers.add_parser("list-assets", help="List assets with active status projection")
    list_parser.add_argument("--library-root", required=True, help="Library root directory")

    switch_parser = subparsers.add_parser("switch-recipe", help="Switch active recipe and schedule reindex jobs")
    switch_parser.add_argument("--library-root", required=True, help="Library root directory")
    switch_parser.add_argument(
        "--preset",
        required=True,
        choices=(
            "qwen3-2b-cpu",
            "qwen3-8b-cpu",
            "qwen3-2b-cuda-balanced",
            "qwen3-8b-cuda-balanced",
            "qwen3-2b-cuda-quality",
            "qwen3-8b-cuda-quality",
        ),
        help="Named recipe preset",
    )
    switch_parser.add_argument(
        "--gif-frame-count",
        type=int,
        default=None,
        help="Optional GIF frame count override for the target recipe.",
    )

    run_jobs_parser = subparsers.add_parser("run-jobs", help="Execute pending jobs")
    run_jobs_parser.add_argument("--library-root", required=True, help="Library root directory")
    _add_embedding_runtime_args(run_jobs_parser)
    run_jobs_parser.add_argument("--max-jobs", type=int, default=None, help="Optional job limit")

    search_parser = subparsers.add_parser("search", help="Search assets with the active recipe")
    search_parser.add_argument("--library-root", required=True, help="Library root directory")
    search_parser.add_argument("--query", required=True, help="Text query")
    search_parser.add_argument("--top-k", type=int, default=10, help="Result count")
    _add_embedding_runtime_args(search_parser)

    image_search_parser = subparsers.add_parser("search-image", help="Search assets with a local image file")
    image_search_parser.add_argument("--library-root", required=True, help="Library root directory")
    image_search_parser.add_argument("--path", required=True, help="Local image or GIF path")
    image_search_parser.add_argument("--top-k", type=int, default=10, help="Result count")
    _add_embedding_runtime_args(image_search_parser)

    similar_parser = subparsers.add_parser("find-similar", help="Find assets similar to one asset id")
    similar_parser.add_argument("--library-root", required=True, help="Library root directory")
    similar_parser.add_argument("--asset-id", required=True, help="Query asset id")
    similar_parser.add_argument("--top-k", type=int, default=10, help="Result count")

    web_parser = subparsers.add_parser("serve-web", help="Serve the local MVP web app")
    web_parser.add_argument("--library-root", required=True, help="Library root directory")
    web_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    web_parser.add_argument("--port", type=int, default=8765, help="Bind port")

    launch_parser = subparsers.add_parser(
        "launch-app",
        help="Launch the local MVP app with a default Windows library root",
    )
    launch_parser.add_argument(
        "--library-root",
        default=None,
        help="Optional library root directory. Defaults to AppData\\Roaming\\MemeSort.",
    )
    launch_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    launch_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Preferred bind port. Falls back to a random free port when busy.",
    )
    launch_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the local app in the default browser.",
    )

    desktop_parser = subparsers.add_parser(
        "desktop-shell",
        help="Open the thin native Windows launcher for the local MVP app",
    )

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-library":
        result = initialize_library(args.root)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "import-folder":
        result = import_folder(args.library_root, args.path)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "list-assets":
        result = list_assets(args.library_root)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "switch-recipe":
        result = switch_active_recipe(
            args.library_root,
            args.preset,
            gif_frame_count=args.gif_frame_count,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "run-jobs":
        result = run_pending_jobs(
            args.library_root,
            backend_name=args.backend,
            model_name_or_path=args.model_name_or_path,
            torch_dtype=args.torch_dtype,
            device=args.device,
            num_threads=args.num_threads,
            num_interop_threads=args.num_interop_threads,
            max_jobs=args.max_jobs,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "search":
        result = search_text(
            args.library_root,
            query=args.query,
            top_k=args.top_k,
            backend_name=args.backend,
            model_name_or_path=args.model_name_or_path,
            torch_dtype=args.torch_dtype,
            device=args.device,
            num_threads=args.num_threads,
            num_interop_threads=args.num_interop_threads,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "search-image":
        result = search_image_path(
            args.library_root,
            image_path=args.path,
            top_k=args.top_k,
            backend_name=args.backend,
            model_name_or_path=args.model_name_or_path,
            torch_dtype=args.torch_dtype,
            device=args.device,
            num_threads=args.num_threads,
            num_interop_threads=args.num_interop_threads,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "find-similar":
        result = find_similar_assets(
            args.library_root,
            asset_id=args.asset_id,
            top_k=args.top_k,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "serve-web":
        run_web_app(
            args.library_root,
            host=args.host,
            port=args.port,
        )
        return 0

    if args.command == "launch-app":
        launch_local_mvp_app(
            library_root=args.library_root,
            host=args.host,
            preferred_port=args.port,
            open_browser=not args.no_browser,
        )
        return 0

    if args.command == "desktop-shell":
        launch_desktop_shell()
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def main() -> None:
    raise SystemExit(run())
