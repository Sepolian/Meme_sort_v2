from __future__ import annotations

import argparse
import json
from typing import Sequence

from .desktop_app import launch_desktop_shell
from .launcher import launch_local_mvp_app
from .app_commands import (
    run_jobs,
    search_image,
    search_text,
)
from .library import (
    import_folder,
    initialize_library,
    list_assets,
)
from .runtime_service import run_runtime_health_check
from .retrieval_service import find_similar_assets
from .webapp import run_web_app


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

    run_jobs_parser = subparsers.add_parser("run-jobs", help="Execute pending jobs")
    run_jobs_parser.add_argument("--library-root", required=True, help="Library root directory")
    run_jobs_parser.add_argument("--max-jobs", type=int, default=None, help="Optional job limit")

    search_parser = subparsers.add_parser("search", help="Search assets with the active recipe")
    search_parser.add_argument("--library-root", required=True, help="Library root directory")
    search_parser.add_argument("--query", required=True, help="Text query")
    search_parser.add_argument("--top-k", type=int, default=10, help="Result count")

    image_search_parser = subparsers.add_parser("search-image", help="Search assets with a local image file")
    image_search_parser.add_argument("--library-root", required=True, help="Library root directory")
    image_search_parser.add_argument("--path", required=True, help="Local image or GIF path")
    image_search_parser.add_argument("--top-k", type=int, default=10, help="Result count")

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

    if args.command == "run-jobs":
        health_check = run_runtime_health_check(args.library_root)
        if not health_check.smoke_test_ok:
            parser.error(
                health_check.error
                or "Vulkan runtime health check failed; indexing was not started."
            )
        result = run_jobs(
            args.library_root,
            max_jobs=args.max_jobs,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "search":
        result = search_text(
            args.library_root,
            query=args.query,
            top_k=args.top_k,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "search-image":
        result = search_image(
            args.library_root,
            image_path=args.path,
            top_k=args.top_k,
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
