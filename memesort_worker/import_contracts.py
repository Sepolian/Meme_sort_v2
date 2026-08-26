"""Public result and error contracts for multi-source Import Batches."""

from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass
from enum import StrEnum


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


def _display_safe_text(value: object, *, fallback: str, max_length: int) -> str:
    text = _CONTROL_CHARACTERS.sub(" ", str(value))
    text = " ".join(text.split())
    if not text:
        return fallback
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rstrip()}…"


class ImportFailureStage(StrEnum):
    """Stable stage names for a reportable Import Failure."""

    SCAN = "scan"
    READ = "read"
    COPY = "copy"
    VALIDATION = "validation"
    DATABASE = "database"


class ImportFailureCode(StrEnum):
    """Stable, machine-readable Import Failure codes."""

    SCAN_FAILED = "scan_failed"
    FILE_LIMIT_EXCEEDED = "file_limit_exceeded"

    SOURCE_MISSING = "source_missing"
    SOURCE_NOT_REGULAR_FILE = "source_not_regular_file"
    SOURCE_REPARSE_POINT = "source_reparse_point"
    SOURCE_READ_FAILED = "source_read_failed"
    SOURCE_TOO_LARGE = "source_too_large"

    LIBRARY_COPY_FAILED = "library_copy_failed"
    LIBRARY_COPY_HASH_MISMATCH = "library_copy_hash_mismatch"
    LIBRARY_COPY_TOO_LARGE = "library_copy_too_large"

    IMAGE_DECODE_FAILED = "image_decode_failed"
    IMAGE_FRAME_TOO_LARGE = "image_frame_too_large"
    GIF_FRAME_LIMIT_EXCEEDED = "gif_frame_limit_exceeded"

    DATABASE_WRITE_FAILED = "database_write_failed"


class ImportBatchErrorCode(StrEnum):
    """Stable, machine-readable fatal Import Batch error codes."""

    INVALID_SOURCE = "invalid_source"
    FILE_LIMIT_EXCEEDED = "file_limit_exceeded"


_CODES_BY_STAGE: dict[ImportFailureStage, frozenset[ImportFailureCode]] = {
    ImportFailureStage.SCAN: frozenset(
        {
            ImportFailureCode.SCAN_FAILED,
            ImportFailureCode.FILE_LIMIT_EXCEEDED,
        }
    ),
    ImportFailureStage.READ: frozenset(
        {
            ImportFailureCode.SOURCE_MISSING,
            ImportFailureCode.SOURCE_NOT_REGULAR_FILE,
            ImportFailureCode.SOURCE_REPARSE_POINT,
            ImportFailureCode.SOURCE_READ_FAILED,
            ImportFailureCode.SOURCE_TOO_LARGE,
        }
    ),
    ImportFailureStage.COPY: frozenset(
        {
            ImportFailureCode.LIBRARY_COPY_FAILED,
            ImportFailureCode.LIBRARY_COPY_HASH_MISMATCH,
            ImportFailureCode.LIBRARY_COPY_TOO_LARGE,
        }
    ),
    ImportFailureStage.VALIDATION: frozenset(
        {
            ImportFailureCode.IMAGE_DECODE_FAILED,
            ImportFailureCode.IMAGE_FRAME_TOO_LARGE,
            ImportFailureCode.GIF_FRAME_LIMIT_EXCEEDED,
        }
    ),
    ImportFailureStage.DATABASE: frozenset(
        {ImportFailureCode.DATABASE_WRITE_FAILED}
    ),
}


@dataclass(frozen=True, kw_only=True)
class ImportFailure:
    """One display-safe failure encountered while scanning or importing."""

    stage: ImportFailureStage
    code: ImportFailureCode
    source_name: str
    detail: str

    def __post_init__(self) -> None:
        stage = ImportFailureStage(self.stage)
        code = ImportFailureCode(self.code)
        if code not in _CODES_BY_STAGE[stage]:
            raise ValueError(
                f"Failure code {code.value!r} does not belong to stage {stage.value!r}"
            )

        safe_source = _display_safe_text(
            self.source_name,
            fallback="Unknown source",
            max_length=255,
        )
        safe_source = ntpath.basename(safe_source.rstrip("\\/")) or "Unknown source"

        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "source_name", safe_source)
        object.__setattr__(
            self,
            "detail",
            _display_safe_text(
                self.detail,
                fallback="No additional detail.",
                max_length=500,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage.value,
            "code": self.code.value,
            "source_name": self.source_name,
            "detail": self.detail,
        }


MAX_IMPORT_FAILURE_DETAILS = 100


@dataclass(frozen=True, kw_only=True)
class ImportBatchResult:
    """Counters and bounded failure details produced by one Import Batch.

    The contract enforces these arithmetic invariants:

    - ``discovered_files == supported_files + unsupported_files``;
    - ``processed_files == succeeded_files + failed_files`` and never exceeds
      ``supported_files`` (a fatal or cancelled batch may leave files unprocessed);
    - ``succeeded_files == new_assets + duplicate_assets``;
    - every succeeded file adds or refreshes exactly one Source Record; and
    - ``failure_count == scan_failures + failed_files``. Unsupported files and
      reparse-point skips are therefore not Import Failures.

    Every failure is retained until the public detail limit is reached. The
    full failure count remains available after details are truncated.
    """

    library_root: str
    selected_sources: int
    effective_sources: int
    discovered_files: int
    supported_files: int
    unsupported_files: int
    reparse_points_skipped: int
    scan_failures: int
    processed_files: int
    succeeded_files: int
    failed_files: int
    new_assets: int
    duplicate_assets: int
    source_records_added: int
    source_records_refreshed: int
    jobs_created: int
    failure_details: tuple[ImportFailure, ...] = ()
    active_recipe_id: str | None = None

    def __post_init__(self) -> None:
        count_names = (
            "selected_sources",
            "effective_sources",
            "discovered_files",
            "supported_files",
            "unsupported_files",
            "reparse_points_skipped",
            "scan_failures",
            "processed_files",
            "succeeded_files",
            "failed_files",
            "new_assets",
            "duplicate_assets",
            "source_records_added",
            "source_records_refreshed",
            "jobs_created",
        )
        for name in count_names:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        if self.selected_sources == 0:
            raise ValueError("selected_sources must be greater than zero")
        if self.effective_sources == 0:
            raise ValueError("effective_sources must be greater than zero")
        if self.effective_sources > self.selected_sources:
            raise ValueError("effective_sources cannot exceed selected_sources")
        if self.discovered_files != self.supported_files + self.unsupported_files:
            raise ValueError(
                "discovered_files must equal supported_files + unsupported_files"
            )
        if self.processed_files > self.supported_files:
            raise ValueError("processed_files cannot exceed supported_files")
        if self.processed_files != self.succeeded_files + self.failed_files:
            raise ValueError(
                "processed_files must equal succeeded_files + failed_files"
            )
        if self.succeeded_files != self.new_assets + self.duplicate_assets:
            raise ValueError(
                "succeeded_files must equal new_assets + duplicate_assets"
            )
        if self.succeeded_files != (
            self.source_records_added + self.source_records_refreshed
        ):
            raise ValueError(
                "succeeded_files must equal source_records_added + "
                "source_records_refreshed"
            )

        failure_details = tuple(self.failure_details)
        if any(not isinstance(failure, ImportFailure) for failure in failure_details):
            raise ValueError("failure_details must contain only ImportFailure values")
        retained_failure_count = min(self.failure_count, MAX_IMPORT_FAILURE_DETAILS)
        if len(failure_details) < retained_failure_count:
            raise ValueError(
                "failure_details must retain every failure up to the detail limit"
            )
        if len(failure_details) > self.failure_count:
            raise ValueError("failure_details cannot exceed failure_count")

        retained_details = failure_details[:MAX_IMPORT_FAILURE_DETAILS]
        scan_details = sum(
            failure.stage is ImportFailureStage.SCAN for failure in retained_details
        )
        if scan_details > self.scan_failures:
            raise ValueError("scan failure details cannot exceed scan_failures")
        if len(retained_details) - scan_details > self.failed_files:
            raise ValueError("processing failure details cannot exceed failed_files")
        object.__setattr__(self, "failure_details", retained_details)

    @property
    def failure_count(self) -> int:
        return self.scan_failures + self.failed_files

    @property
    def failures_truncated(self) -> bool:
        return self.failure_count > len(self.failure_details)

    def to_dict(self) -> dict[str, object]:
        return {
            "library_root": self.library_root,
            "selected_sources": self.selected_sources,
            "effective_sources": self.effective_sources,
            "discovered_files": self.discovered_files,
            "supported_files": self.supported_files,
            "unsupported_files": self.unsupported_files,
            "reparse_points_skipped": self.reparse_points_skipped,
            "scan_failures": self.scan_failures,
            "processed_files": self.processed_files,
            "succeeded_files": self.succeeded_files,
            "failed_files": self.failed_files,
            "new_assets": self.new_assets,
            "duplicate_assets": self.duplicate_assets,
            "source_records_added": self.source_records_added,
            "source_records_refreshed": self.source_records_refreshed,
            "jobs_created": self.jobs_created,
            "failure_count": self.failure_count,
            "failure_details": [failure.to_dict() for failure in self.failure_details],
            "failures_truncated": self.failures_truncated,
            "active_recipe_id": self.active_recipe_id,
        }


@dataclass(frozen=True, kw_only=True)
class ImportProgress:
    """Progress emitted by the synchronous Import Batch seam.

    The controller uses these fields to publish a snapshot without waiting
    for the batch to finish.  ``current_source_name`` is always a basename;
    full filesystem paths are not part of the public progress contract.
    """

    phase: str
    current_source_name: str | None
    selected_sources: int = 0
    effective_sources: int = 0
    discovered_files: int = 0
    supported_files: int = 0
    unsupported_files: int = 0
    reparse_points_skipped: int = 0
    scan_failures: int = 0
    processed_files: int = 0
    succeeded_files: int = 0
    failed_files: int = 0
    new_assets: int = 0
    duplicate_assets: int = 0
    source_records_added: int = 0
    source_records_refreshed: int = 0
    jobs_created: int = 0
    failure_details: tuple[ImportFailure, ...] = ()

    def __post_init__(self) -> None:
        if self.phase not in {"scanning", "importing"}:
            raise ValueError("ImportProgress phase must be scanning or importing")

        count_names = (
            "selected_sources",
            "effective_sources",
            "discovered_files",
            "supported_files",
            "unsupported_files",
            "reparse_points_skipped",
            "scan_failures",
            "processed_files",
            "succeeded_files",
            "failed_files",
            "new_assets",
            "duplicate_assets",
            "source_records_added",
            "source_records_refreshed",
            "jobs_created",
        )
        for name in count_names:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.discovered_files != self.supported_files + self.unsupported_files:
            raise ValueError(
                "discovered_files must equal supported_files + unsupported_files"
            )
        if self.processed_files != self.succeeded_files + self.failed_files:
            raise ValueError(
                "processed_files must equal succeeded_files + failed_files"
            )
        if self.succeeded_files != self.new_assets + self.duplicate_assets:
            raise ValueError("succeeded_files must equal new_assets + duplicate_assets")

        failure_details = tuple(self.failure_details)
        if any(not isinstance(failure, ImportFailure) for failure in failure_details):
            raise ValueError("failure_details must contain only ImportFailure values")
        object.__setattr__(self, "failure_details", failure_details)

        if self.current_source_name is not None:
            safe_name = _display_safe_text(
                self.current_source_name,
                fallback="Unknown source",
                max_length=255,
            )
            safe_name = ntpath.basename(safe_name.rstrip("\\/")) or "Unknown source"
            object.__setattr__(self, "current_source_name", safe_name)


class ImportBatchError(RuntimeError):
    """Fatal Import Batch error, optionally retaining committed partial work."""

    def __init__(
        self,
        *,
        code: ImportBatchErrorCode,
        detail: str,
        partial_result: ImportBatchResult | None = None,
    ) -> None:
        error_code = ImportBatchErrorCode(code)
        safe_detail = _display_safe_text(
            detail,
            fallback="The Import Batch could not be completed.",
            max_length=500,
        )
        super().__init__(safe_detail)
        self.code = error_code
        self.detail = safe_detail
        self.partial_result = partial_result

    def to_dict(self) -> dict[str, object]:
        return {
            "error": "import_batch_failed",
            "code": self.code.value,
            "detail": self.detail,
            "partial_result": (
                self.partial_result.to_dict()
                if self.partial_result is not None
                else None
            ),
        }


class ImportBatchPreflightError(ImportBatchError):
    """Top-level source validation failure that occurs before batch work."""

    def __init__(self, *, code: ImportBatchErrorCode, detail: str) -> None:
        super().__init__(code=code, detail=detail, partial_result=None)

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        payload["error"] = "import_batch_preflight_failed"
        return payload
