"""PyInstaller entry point for the headless Tauri sidecar."""

from memesort_worker.sidecar_entry import main


if __name__ == "__main__":
    raise SystemExit(main())
