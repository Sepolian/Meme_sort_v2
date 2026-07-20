from __future__ import annotations

import os
import sys

from memesort_worker.desktop_app import launch_desktop_shell, run_smoke_test


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    library_root = os.environ.get("MEMESORT_LIBRARY_ROOT") or None
    if "--smoke-test" in args:
        return run_smoke_test(library_root=library_root)

    import_source = os.environ.get("MEMESORT_IMPORT_SOURCE") or None
    use_browser = "--browser" in args or _env_flag("MEMESORT_BROWSER_FALLBACK")
    return launch_desktop_shell(
        library_root=library_root,
        import_source=import_source,
        use_browser=use_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
