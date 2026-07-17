from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .runtime_manifest import RuntimeManifest, load_runtime_manifest


ACTIVATION_SCHEMA_VERSION = 1


class RuntimeActivationError(RuntimeError):
    pass


def expected_activation_record(manifest: RuntimeManifest) -> dict[str, Any]:
    return {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "runtime_fingerprint": manifest.runtime_fingerprint,
        "llama_cpp": {
            "build": manifest.llama_cpp.build,
            "executable": manifest.llama_cpp.executable,
            "archive_sha256": manifest.llama_cpp.archive.sha256,
        },
        "model": {
            "main": {
                "filename": manifest.model.main.filename,
                "sha256": manifest.model.main.sha256,
                "size_bytes": manifest.model.main.size_bytes,
            },
            "projector": {
                "filename": manifest.model.projector.filename,
                "sha256": manifest.model.projector.sha256,
                "size_bytes": manifest.model.projector.size_bytes,
            },
        },
    }


def validate_runtime_activation(manifest: RuntimeManifest) -> None:
    path = manifest.activation_record_path
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeActivationError(
            f"Runtime is not activated: {path} is missing. Run setup."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeActivationError(
            f"Runtime activation record is invalid JSON: {path}. Run setup."
        ) from exc
    expected = expected_activation_record(manifest)
    if record != expected:
        raise RuntimeActivationError(
            "Runtime activation does not match runtime-manifest.json. Run setup to "
            "verify and atomically reactivate the pinned runtime."
        )


def write_runtime_activation(manifest: RuntimeManifest) -> Path:
    path = manifest.activation_record_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(
        expected_activation_record(manifest),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    try:
        temporary.write_text(f"{payload}\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage MemeSort runtime activation")
    parser.add_argument("action", choices=("validate", "write"))
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = load_runtime_manifest(args.manifest)
    if args.action == "write":
        print(write_runtime_activation(manifest))
    else:
        validate_runtime_activation(manifest)
        print(manifest.activation_record_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
