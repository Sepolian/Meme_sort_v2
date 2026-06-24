from __future__ import annotations

import os

from memesort_worker.desktop_app import launch_desktop_shell


def main() -> None:
    library_root = os.environ.get("MEMESORT_LIBRARY_ROOT") or None
    import_source = os.environ.get("MEMESORT_IMPORT_SOURCE") or None
    autostart_ui = os.environ.get("MEMESORT_AUTOSTART_UI", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    open_ui_on_ready = os.environ.get("MEMESORT_OPEN_BROWSER", "").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    launch_desktop_shell(
        library_root=library_root,
        autostart_ui=autostart_ui,
        import_source=import_source,
        open_ui_on_ready=open_ui_on_ready,
    )


if __name__ == "__main__":
    main()
