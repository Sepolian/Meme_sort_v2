from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from memesort_worker.indexing_pipeline import run_pending_jobs
from memesort_worker.library import (
    import_folder,
    initialize_library,
    list_assets,
    search_text,
)
from memesort_worker.pinned_runtime import PinnedRuntime
from memesort_worker.runtime_manifest import load_runtime_manifest


DEFAULT_QUERY_FIELDS = (
    "ocr_translation",
    "people_appearance",
    "objects",
    "scene_context",
    "themes",
)


@dataclass
class QueryEvaluation:
    asset_filename: str
    query: str
    expected_asset_id: str
    hit_rank: int | None
    top_results: list[dict[str, object]]


@dataclass
class EvaluationReport:
    backend: str
    model_id: str
    recipe_fingerprint: str
    device: str
    preprocess_version: str
    still_max_side: int
    gif_max_side: int
    query_fields: list[str]
    top_k: int
    asset_count: int
    query_count: int
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    import_seconds: float
    indexing_seconds: float
    query_seconds_total: float
    query_seconds_avg: float
    wall_seconds: float
    per_query: list[QueryEvaluation]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["per_query"] = [asdict(item) for item in self.per_query]
        return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate still-image retrieval against labeled examples")
    parser.add_argument(
        "--dataset-dir",
        default="example",
        help="Directory containing still-image assets",
    )
    parser.add_argument(
        "--labels-path",
        default="labels/labels.json",
        help="JSON labels keyed by filename",
    )
    parser.add_argument(
        "--query-fields",
        nargs="+",
        default=list(DEFAULT_QUERY_FIELDS),
        help="Label fields to concatenate into each text query",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of results to retain per query",
    )
    parser.add_argument(
        "--keep-library",
        action="store_true",
        help="Keep the temporary library directory for inspection",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the JSON report",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    wall_started_at = time.perf_counter()

    dataset_dir = Path(args.dataset_dir).resolve()
    labels_path = Path(args.labels_path).resolve()
    labels = json.loads(labels_path.read_text(encoding="utf-8"))

    manifest = load_runtime_manifest()
    temp_root = Path(tempfile.mkdtemp(prefix="memesort_eval_"))
    library_root = temp_root / "library"

    try:
        initialize_library(library_root)
        runtime = PinnedRuntime(library_root)
        runtime.authorize()

        import_started_at = time.perf_counter()
        import_folder(library_root, dataset_dir)
        import_seconds = time.perf_counter() - import_started_at

        indexing_started_at = time.perf_counter()
        run_pending_jobs(library_root, runtime)
        indexing_seconds = time.perf_counter() - indexing_started_at

        asset_listing = list_assets(library_root)
        filename_to_asset: dict[str, str] = {}
        for asset in asset_listing.assets:
            asset_id = str(asset["asset_id"])
            for source_record in asset["source_records"]:
                filename = Path(str(source_record["source_path"])).name
                if filename in filename_to_asset and filename_to_asset[filename] != asset_id:
                    raise ValueError(
                        f"Filename {filename} maps to multiple assets; labels need a more stable key"
                    )
                filename_to_asset[filename] = asset_id

        per_query: list[QueryEvaluation] = []
        hit_ranks: list[int | None] = []
        query_seconds_total = 0.0

        for filename, label_payload in labels.items():
            expected_asset_id = filename_to_asset.get(filename)
            if expected_asset_id is None:
                raise ValueError(f"Label references missing imported asset: {filename}")

            query = build_query_text(label_payload, args.query_fields)
            query_started_at = time.perf_counter()
            result = search_text(
                library_root,
                query=query,
                top_k=args.top_k,
            )
            query_seconds_total += time.perf_counter() - query_started_at

            hit_rank = None
            for index, row in enumerate(result.results, start=1):
                if str(row["asset_id"]) == expected_asset_id:
                    hit_rank = index
                    break

            hit_ranks.append(hit_rank)
            per_query.append(
                QueryEvaluation(
                    asset_filename=filename,
                    query=query,
                    expected_asset_id=expected_asset_id,
                    hit_rank=hit_rank,
                    top_results=result.results,
                )
            )

        report = EvaluationReport(
            backend="llama.cpp-vulkan",
            model_id=manifest.model.id,
            recipe_fingerprint=manifest.recipe_fingerprint,
            device=manifest.platform.device,
            preprocess_version=manifest.preprocessing.version,
            still_max_side=manifest.preprocessing.still_max_side,
            gif_max_side=manifest.preprocessing.gif_max_side,
            query_fields=list(args.query_fields),
            top_k=args.top_k,
            asset_count=len(filename_to_asset),
            query_count=len(per_query),
            recall_at_1=_recall_at_k(hit_ranks, 1),
            recall_at_5=_recall_at_k(hit_ranks, 5),
            recall_at_10=_recall_at_k(hit_ranks, 10),
            import_seconds=round(import_seconds, 3),
            indexing_seconds=round(indexing_seconds, 3),
            query_seconds_total=round(query_seconds_total, 3),
            query_seconds_avg=round(query_seconds_total / len(per_query), 3) if per_query else 0.0,
            wall_seconds=round(time.perf_counter() - wall_started_at, 3),
            per_query=per_query,
        )

        payload = report.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.output:
            output_path = Path(args.output).resolve()
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if args.keep_library:
            print(json.dumps({"kept_library_root": str(library_root.resolve())}, ensure_ascii=False))
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def build_query_text(label_payload: dict[str, object], fields: Iterable[str]) -> str:
    parts: list[str] = []
    for field in fields:
        value = label_payload.get(field)
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        elif value is None:
            text = ""
        else:
            text = str(value).strip()
        if text:
            parts.append(text)
    if not parts:
        raise ValueError("Label payload did not produce any query text")
    return "\n".join(parts)


def _recall_at_k(hit_ranks: list[int | None], k: int) -> float:
    if not hit_ranks:
        return 0.0
    hits = sum(1 for rank in hit_ranks if rank is not None and rank <= k)
    return hits / len(hit_ranks)


if __name__ == "__main__":
    main()
