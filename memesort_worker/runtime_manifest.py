from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit

from .app_paths import AppPaths, ENV_PORTABLE_ROOT


MANIFEST_FILENAME = "runtime-manifest.json"
SUPPORTED_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_VENDOR_RE = re.compile(r"^0x[0-9a-f]{4}$")


class RuntimeManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactSpec:
    filename: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class RuntimePaths:
    download_dir: str
    activation_record: str
    log_dir: str


@dataclass(frozen=True)
class PlatformSpec:
    os: str
    architecture: str
    device: str
    vendor_ids: Mapping[str, str]


@dataclass(frozen=True)
class UvSpec:
    version: str
    install_dir: str
    archive: ArtifactSpec


@dataclass(frozen=True)
class PythonSpec:
    main_version: str
    ocr_version: str


@dataclass(frozen=True)
class ToolchainSpec:
    uv: UvSpec
    python: PythonSpec


@dataclass(frozen=True)
class LlamaServerSpec:
    gpu_layers: int
    context_size: int
    parallel_slots: int
    startup_timeout_seconds: float
    request_timeout_seconds: float
    device_probe_timeout_seconds: float
    idle_timeout_seconds: float


@dataclass(frozen=True)
class LlamaCppSpec:
    build: str
    install_dir: str
    executable: str
    archive: ArtifactSpec
    server: LlamaServerSpec


@dataclass(frozen=True)
class ModelSpec:
    id: str
    base_model_id: str
    conversion_repository: str
    install_dir: str
    protocol: str
    request_model: str
    output_dimension: int
    main: ArtifactSpec
    projector: ArtifactSpec


@dataclass(frozen=True)
class PreprocessingSpec:
    version: str
    still_max_side: int
    gif_max_side: int
    gif_frame_count: int
    resample: str
    color_mode: str
    alpha_background: str
    apply_exif_orientation: bool


@dataclass(frozen=True)
class EmbeddingSpec:
    instruction_id: str
    instruction: str
    media_marker: str
    pooling: str
    normalization: str
    storage_dtype: str


@dataclass(frozen=True)
class LoggingSpec:
    file_count: int
    max_bytes_per_file: int


@dataclass(frozen=True)
class RuntimeManifest:
    source_path: Path
    schema_version: int
    paths: RuntimePaths
    platform: PlatformSpec
    toolchain: ToolchainSpec
    llama_cpp: LlamaCppSpec
    model: ModelSpec
    preprocessing: PreprocessingSpec
    embedding: EmbeddingSpec
    logging: LoggingSpec
    shutdown_grace_seconds: float
    portable_data_root: Path | None = None

    @property
    def project_root(self) -> Path:
        return self.source_path.parent

    @property
    def download_dir(self) -> Path:
        return self._resolve_managed_path(self.paths.download_dir)

    @property
    def activation_record_path(self) -> Path:
        return self._resolve_managed_path(self.paths.activation_record)

    @property
    def log_dir(self) -> Path:
        return self._resolve_managed_path(self.paths.log_dir)

    @property
    def uv_install_dir(self) -> Path:
        return self._resolve_managed_path(self.toolchain.uv.install_dir)

    @property
    def llama_install_dir(self) -> Path:
        return self._resolve_managed_path(self.llama_cpp.install_dir)

    @property
    def llama_server_path(self) -> Path:
        return self.llama_install_dir / self.llama_cpp.executable

    @property
    def model_install_dir(self) -> Path:
        return self._resolve_managed_path(self.model.install_dir)

    @property
    def main_model_path(self) -> Path:
        return self.model_install_dir / self.model.main.filename

    @property
    def projector_path(self) -> Path:
        return self.model_install_dir / self.model.projector.filename

    def _resolve_managed_path(self, relative: str) -> Path:
        if self.portable_data_root is None:
            return _resolve_relative(self.project_root, relative)
        return _resolve_portable_relative(self.portable_data_root, relative)

    @property
    def recipe_payload(self) -> dict[str, Any]:
        """Return only fields capable of changing persisted embedding meaning."""
        return {
            "recipe_schema_version": 1,
            "llama_cpp": {
                "build": self.llama_cpp.build,
                "archive_sha256": self.llama_cpp.archive.sha256,
                "context_size": self.llama_cpp.server.context_size,
            },
            "model": {
                "id": self.model.id,
                "protocol": self.model.protocol,
                "request_model": self.model.request_model,
                "output_dimension": self.model.output_dimension,
                "main_sha256": self.model.main.sha256,
                "projector_sha256": self.model.projector.sha256,
            },
            "preprocessing": {
                "version": self.preprocessing.version,
                "still_max_side": self.preprocessing.still_max_side,
                "gif_max_side": self.preprocessing.gif_max_side,
                "gif_frame_count": self.preprocessing.gif_frame_count,
                "resample": self.preprocessing.resample,
                "color_mode": self.preprocessing.color_mode,
                "alpha_background": self.preprocessing.alpha_background,
                "apply_exif_orientation": self.preprocessing.apply_exif_orientation,
            },
            "embedding": {
                "instruction_id": self.embedding.instruction_id,
                "instruction": self.embedding.instruction,
                "media_marker": self.embedding.media_marker,
                "pooling": self.embedding.pooling,
                "normalization": self.embedding.normalization,
                "storage_dtype": self.embedding.storage_dtype,
            },
        }

    @property
    def recipe_fingerprint(self) -> str:
        payload = json.dumps(
            self.recipe_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def recipe_id(self) -> str:
        return f"vulkan-{self.recipe_fingerprint}"

    @property
    def recipe_display_id(self) -> str:
        return f"vulkan-{self.recipe_fingerprint[:8]}"

    @property
    def runtime_payload(self) -> dict[str, Any]:
        """Return fields whose change requires setup to reactivate the runtime."""
        return {
            "runtime_schema_version": 1,
            "platform": {
                "os": self.platform.os,
                "architecture": self.platform.architecture,
                "device": self.platform.device,
                "vendor_ids": dict(self.platform.vendor_ids),
            },
            "toolchain": {
                "uv_version": self.toolchain.uv.version,
                "uv_archive_sha256": self.toolchain.uv.archive.sha256,
                "uv_archive_size_bytes": self.toolchain.uv.archive.size_bytes,
                "python_main_version": self.toolchain.python.main_version,
                "python_ocr_version": self.toolchain.python.ocr_version,
            },
            "llama_cpp": {
                "build": self.llama_cpp.build,
                "archive_filename": self.llama_cpp.archive.filename,
                "archive_sha256": self.llama_cpp.archive.sha256,
                "archive_size_bytes": self.llama_cpp.archive.size_bytes,
                "executable": self.llama_cpp.executable,
            },
            "model": {
                "main_filename": self.model.main.filename,
                "main_sha256": self.model.main.sha256,
                "main_size_bytes": self.model.main.size_bytes,
                "projector_filename": self.model.projector.filename,
                "projector_sha256": self.model.projector.sha256,
                "projector_size_bytes": self.model.projector.size_bytes,
            },
        }

    @property
    def runtime_fingerprint(self) -> str:
        payload = json.dumps(
            self.runtime_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def default_manifest_path() -> Path:
    return AppPaths.discover().manifest_path


def load_runtime_manifest(
    path: Path | str | None = None,
    *,
    portable_data_root: Path | str | None = None,
) -> RuntimeManifest:
    source_path = Path(path or default_manifest_path()).expanduser().resolve()
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeManifestError(f"Runtime manifest does not exist: {source_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeManifestError(
            f"Runtime manifest is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeManifestError("Runtime manifest root must be an object")
    if portable_data_root is not None:
        resolved_data_root = Path(portable_data_root).expanduser().resolve()
    elif os.environ.get(ENV_PORTABLE_ROOT):
        resolved_data_root = AppPaths.discover().data_root
    else:
        resolved_data_root = None
    return _parse_manifest(source_path, raw, portable_data_root=resolved_data_root)


def _parse_manifest(
    source_path: Path,
    raw: Mapping[str, Any],
    *,
    portable_data_root: Path | None = None,
) -> RuntimeManifest:
    _expect_keys(
        raw,
        {
            "schema_version",
            "paths",
            "platform",
            "toolchain",
            "llama_cpp",
            "model",
            "preprocessing",
            "embedding",
            "logging",
            "shutdown_grace_seconds",
        },
        "manifest",
    )
    schema_version = _positive_int(raw["schema_version"], "schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise RuntimeManifestError(
            f"Unsupported runtime manifest schema_version {schema_version}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}"
        )

    paths_raw = _object(raw["paths"], "paths")
    _expect_keys(paths_raw, {"download_dir", "activation_record", "log_dir"}, "paths")
    paths = RuntimePaths(
        download_dir=_relative_path(paths_raw["download_dir"], "paths.download_dir"),
        activation_record=_relative_path(
            paths_raw["activation_record"], "paths.activation_record"
        ),
        log_dir=_relative_path(paths_raw["log_dir"], "paths.log_dir"),
    )

    platform_raw = _object(raw["platform"], "platform")
    _expect_keys(
        platform_raw, {"os", "architecture", "device", "vendor_ids"}, "platform"
    )
    vendor_ids_raw = _object(platform_raw["vendor_ids"], "platform.vendor_ids")
    _expect_keys(vendor_ids_raw, {"amd", "intel", "nvidia"}, "platform.vendor_ids")
    vendor_ids: dict[str, str] = {}
    for vendor, value in vendor_ids_raw.items():
        vendor_id = _string(value, f"platform.vendor_ids.{vendor}").lower()
        if not _HEX_VENDOR_RE.fullmatch(vendor_id):
            raise RuntimeManifestError(
                f"platform.vendor_ids.{vendor} must use 0x0000 hexadecimal form"
            )
        vendor_ids[vendor] = vendor_id
    platform = PlatformSpec(
        os=_literal(platform_raw["os"], "windows", "platform.os"),
        architecture=_literal(
            platform_raw["architecture"], "x86_64", "platform.architecture"
        ),
        device=_literal(platform_raw["device"], "Vulkan0", "platform.device"),
        vendor_ids=vendor_ids,
    )

    toolchain_raw = _object(raw["toolchain"], "toolchain")
    _expect_keys(toolchain_raw, {"uv", "python"}, "toolchain")
    uv_raw = _object(toolchain_raw["uv"], "toolchain.uv")
    _expect_keys(uv_raw, {"version", "install_dir", "archive"}, "toolchain.uv")
    python_raw = _object(toolchain_raw["python"], "toolchain.python")
    _expect_keys(
        python_raw, {"main_version", "ocr_version"}, "toolchain.python"
    )
    toolchain = ToolchainSpec(
        uv=UvSpec(
            version=_version(uv_raw["version"], "toolchain.uv.version"),
            install_dir=_relative_path(
                uv_raw["install_dir"], "toolchain.uv.install_dir"
            ),
            archive=_artifact(uv_raw["archive"], "toolchain.uv.archive"),
        ),
        python=PythonSpec(
            main_version=_version(
                python_raw["main_version"], "toolchain.python.main_version"
            ),
            ocr_version=_version(
                python_raw["ocr_version"], "toolchain.python.ocr_version"
            ),
        ),
    )

    llama_raw = _object(raw["llama_cpp"], "llama_cpp")
    _expect_keys(
        llama_raw,
        {"build", "install_dir", "executable", "archive", "server"},
        "llama_cpp",
    )
    server_raw = _object(llama_raw["server"], "llama_cpp.server")
    _expect_keys(
        server_raw,
        {
            "gpu_layers",
            "context_size",
            "parallel_slots",
            "startup_timeout_seconds",
            "request_timeout_seconds",
            "device_probe_timeout_seconds",
            "idle_timeout_seconds",
        },
        "llama_cpp.server",
    )
    llama_cpp = LlamaCppSpec(
        build=_string(llama_raw["build"], "llama_cpp.build"),
        install_dir=_relative_path(llama_raw["install_dir"], "llama_cpp.install_dir"),
        executable=_filename(llama_raw["executable"], "llama_cpp.executable"),
        archive=_artifact(llama_raw["archive"], "llama_cpp.archive"),
        server=LlamaServerSpec(
            gpu_layers=_positive_int(
                server_raw["gpu_layers"], "llama_cpp.server.gpu_layers"
            ),
            context_size=_positive_int(
                server_raw["context_size"], "llama_cpp.server.context_size"
            ),
            parallel_slots=_positive_int(
                server_raw["parallel_slots"], "llama_cpp.server.parallel_slots"
            ),
            startup_timeout_seconds=_positive_number(
                server_raw["startup_timeout_seconds"],
                "llama_cpp.server.startup_timeout_seconds",
            ),
            request_timeout_seconds=_positive_number(
                server_raw["request_timeout_seconds"],
                "llama_cpp.server.request_timeout_seconds",
            ),
            device_probe_timeout_seconds=_positive_number(
                server_raw["device_probe_timeout_seconds"],
                "llama_cpp.server.device_probe_timeout_seconds",
            ),
            idle_timeout_seconds=_positive_number(
                server_raw["idle_timeout_seconds"],
                "llama_cpp.server.idle_timeout_seconds",
            ),
        ),
    )

    model_raw = _object(raw["model"], "model")
    _expect_keys(
        model_raw,
        {
            "id",
            "base_model_id",
            "conversion_repository",
            "install_dir",
            "protocol",
            "request_model",
            "output_dimension",
            "main",
            "projector",
        },
        "model",
    )
    model = ModelSpec(
        id=_string(model_raw["id"], "model.id"),
        base_model_id=_string(model_raw["base_model_id"], "model.base_model_id"),
        conversion_repository=_https_url(
            model_raw["conversion_repository"], "model.conversion_repository"
        ),
        install_dir=_relative_path(model_raw["install_dir"], "model.install_dir"),
        protocol=_literal(
            model_raw["protocol"],
            "llama.cpp-openai-multimodal-embeddings-v1",
            "model.protocol",
        ),
        request_model=_string(model_raw["request_model"], "model.request_model"),
        output_dimension=_positive_int(
            model_raw["output_dimension"], "model.output_dimension"
        ),
        main=_artifact(model_raw["main"], "model.main"),
        projector=_artifact(model_raw["projector"], "model.projector"),
    )

    preprocessing_raw = _object(raw["preprocessing"], "preprocessing")
    _expect_keys(
        preprocessing_raw,
        {
            "version",
            "still_max_side",
            "gif_max_side",
            "gif_frame_count",
            "resample",
            "color_mode",
            "alpha_background",
            "apply_exif_orientation",
        },
        "preprocessing",
    )
    preprocessing = PreprocessingSpec(
        version=_string(preprocessing_raw["version"], "preprocessing.version"),
        still_max_side=_positive_int(
            preprocessing_raw["still_max_side"], "preprocessing.still_max_side"
        ),
        gif_max_side=_positive_int(
            preprocessing_raw["gif_max_side"], "preprocessing.gif_max_side"
        ),
        gif_frame_count=_positive_int(
            preprocessing_raw["gif_frame_count"], "preprocessing.gif_frame_count"
        ),
        resample=_literal(
            preprocessing_raw["resample"], "lanczos", "preprocessing.resample"
        ),
        color_mode=_literal(
            preprocessing_raw["color_mode"], "RGB", "preprocessing.color_mode"
        ),
        alpha_background=_literal(
            str(preprocessing_raw["alpha_background"]).lower(),
            "#ffffff",
            "preprocessing.alpha_background",
        ),
        apply_exif_orientation=_boolean(
            preprocessing_raw["apply_exif_orientation"],
            "preprocessing.apply_exif_orientation",
        ),
    )

    embedding_raw = _object(raw["embedding"], "embedding")
    _expect_keys(
        embedding_raw,
        {
            "instruction_id",
            "instruction",
            "media_marker",
            "pooling",
            "normalization",
            "storage_dtype",
        },
        "embedding",
    )
    embedding = EmbeddingSpec(
        instruction_id=_string(
            embedding_raw["instruction_id"], "embedding.instruction_id"
        ),
        instruction=_string(embedding_raw["instruction"], "embedding.instruction"),
        media_marker=_string(
            embedding_raw["media_marker"], "embedding.media_marker"
        ),
        pooling=_literal(embedding_raw["pooling"], "last", "embedding.pooling"),
        normalization=_literal(
            embedding_raw["normalization"], "l2", "embedding.normalization"
        ),
        storage_dtype=_literal(
            embedding_raw["storage_dtype"], "float32", "embedding.storage_dtype"
        ),
    )

    logging_raw = _object(raw["logging"], "logging")
    _expect_keys(logging_raw, {"file_count", "max_bytes_per_file"}, "logging")
    logging = LoggingSpec(
        file_count=_positive_int(logging_raw["file_count"], "logging.file_count"),
        max_bytes_per_file=_positive_int(
            logging_raw["max_bytes_per_file"], "logging.max_bytes_per_file"
        ),
    )

    manifest = RuntimeManifest(
        source_path=source_path,
        schema_version=schema_version,
        paths=paths,
        platform=platform,
        toolchain=toolchain,
        llama_cpp=llama_cpp,
        model=model,
        preprocessing=preprocessing,
        embedding=embedding,
        logging=logging,
        shutdown_grace_seconds=_positive_number(
            raw["shutdown_grace_seconds"], "shutdown_grace_seconds"
        ),
        portable_data_root=portable_data_root,
    )
    _validate_path_collisions(manifest)
    return manifest


def _artifact(value: Any, location: str) -> ArtifactSpec:
    raw = _object(value, location)
    _expect_keys(raw, {"filename", "url", "sha256", "size_bytes"}, location)
    sha256 = _string(raw["sha256"], f"{location}.sha256").lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise RuntimeManifestError(f"{location}.sha256 must be 64 lowercase hex characters")
    return ArtifactSpec(
        filename=_filename(raw["filename"], f"{location}.filename"),
        url=_https_url(raw["url"], f"{location}.url"),
        sha256=sha256,
        size_bytes=_positive_int(raw["size_bytes"], f"{location}.size_bytes"),
    )


def _object(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeManifestError(f"{location} must be an object")
    return value


def _expect_keys(raw: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(raw)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        parts: list[str] = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown {', '.join(unknown)}")
        raise RuntimeManifestError(f"{location} fields invalid: {'; '.join(parts)}")


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeManifestError(f"{location} must be a non-empty string")
    return value.strip()


def _literal(value: Any, expected: str, location: str) -> str:
    actual = _string(value, location)
    if actual != expected:
        raise RuntimeManifestError(f"{location} must be {expected!r}, got {actual!r}")
    return actual


def _positive_int(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeManifestError(f"{location} must be a positive integer")
    return value


def _positive_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RuntimeManifestError(f"{location} must be a positive number")
    return float(value)


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeManifestError(f"{location} must be a boolean")
    return value


def _version(value: Any, location: str) -> str:
    version = _string(value, location)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeManifestError(f"{location} must use exact X.Y.Z form")
    return version


def _filename(value: Any, location: str) -> str:
    filename = _string(value, location)
    path = PurePosixPath(filename.replace("\\", "/"))
    if path.name != filename or len(path.parts) != 1 or filename in {".", ".."}:
        raise RuntimeManifestError(f"{location} must be a filename without directories")
    return filename


def _relative_path(value: Any, location: str) -> str:
    raw = _string(value, location).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeManifestError(f"{location} must be a safe project-relative path")
    if re.match(r"^[A-Za-z]:", raw):
        raise RuntimeManifestError(f"{location} must not contain a drive prefix")
    return path.as_posix()


def _https_url(value: Any, location: str) -> str:
    url = _string(value, location)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeManifestError(f"{location} must be an absolute HTTPS URL")
    return url


def _resolve_relative(root: Path, relative: str) -> Path:
    candidate = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeManifestError(f"Resolved path escapes project root: {relative}") from exc
    return candidate


def _resolve_portable_relative(data_root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    roots = {
        ".runtime": data_root / "runtime",
        ".models": data_root / "models",
    }
    try:
        managed_root = roots[parts[0]]
    except (IndexError, KeyError) as exc:
        raise RuntimeManifestError(
            f"Portable manifest path must start with .runtime or .models: {relative}"
        ) from exc
    candidate = (managed_root / Path(*parts[1:])).resolve()
    try:
        candidate.relative_to(data_root.resolve())
    except ValueError as exc:
        raise RuntimeManifestError(f"Portable manifest path escapes data root: {relative}") from exc
    return candidate


def _validate_path_collisions(manifest: RuntimeManifest) -> None:
    directories = {
        manifest.download_dir,
        manifest.log_dir,
        manifest.uv_install_dir,
        manifest.llama_install_dir,
        manifest.model_install_dir,
    }
    if len(directories) != 5:
        raise RuntimeManifestError("Managed runtime directories must be distinct")
    if manifest.activation_record_path in directories:
        raise RuntimeManifestError("Activation record must be a file outside managed directories")
