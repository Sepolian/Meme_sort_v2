from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from memesort_worker.library import (
    DEFAULT_RECIPE,
    PREPROCESS_SPECS_BY_VERSION,
    RECIPE_PRESETS,
    import_folder,
    initialize_library,
    list_assets,
    run_pending_jobs,
    search_text,
    switch_active_recipe,
)


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
    model_name_or_path: str | None
    torch_dtype: str
    device: str | None
    num_threads: int | None
    num_interop_threads: int | None
    recipe_preset: str | None
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
        "--backend",
        default="debug",
        choices=("debug", "qwen3-vl", "llama.cpp"),
        help="Embedding backend to use",
    )
    parser.add_argument(
        "--model-name-or-path",
        default=None,
        help="Required for qwen3-vl and llama.cpp",
    )
    parser.add_argument(
        "--llama-server",
        default=None,
        help="Path to llama-server.exe (or set MEMESORT_LLAMA_SERVER)",
    )
    parser.add_argument(
        "--torch-dtype",
        default="auto",
        help="Torch dtype for qwen3-vl",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Optional torch device for qwen3-vl",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        default=None,
        help="Optional torch intra-op thread count for qwen3-vl",
    )
    parser.add_argument(
        "--num-interop-threads",
        type=int,
        default=None,
        help="Optional torch inter-op thread count for qwen3-vl",
    )
    parser.add_argument(
        "--recipe-preset",
        default=None,
        help="Optional recipe preset to activate before import/indexing.",
    )
    parser.add_argument(
        "--recipe-runtime-profile",
        default=None,
        help="Optional runtime profile label override for eval-only recipe variants.",
    )
    parser.add_argument(
        "--preprocess-version",
        default=None,
        help="Optional preprocess version override for eval-only recipe variants.",
    )
    parser.add_argument(
        "--still-max-side",
        type=int,
        default=None,
        help="Optional still-image longest-side cap for eval-only recipe variants.",
    )
    parser.add_argument(
        "--gif-max-side",
        type=int,
        default=None,
        help="Optional GIF longest-side cap for eval-only recipe variants.",
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


@dataclass(frozen=True)
class RecipeSelection:
    preset_key: str | None
    preprocess_version: str
    still_max_side: int
    gif_max_side: int


def prepare_recipe_for_eval(
    recipe_preset: str | None,
    recipe_runtime_profile: str | None = None,
    preprocess_version: str | None = None,
    still_max_side: int | None = None,
    gif_max_side: int | None = None,
) -> RecipeSelection:
    if recipe_preset is None:
        if any(
            value is not None
            for value in (
                recipe_runtime_profile,
                preprocess_version,
                still_max_side,
                gif_max_side,
            )
        ):
            raise ValueError("Recipe overrides require --recipe-preset")
        default_preprocess_version = str(DEFAULT_RECIPE["preprocess_version"])
        default_preprocess = PREPROCESS_SPECS_BY_VERSION[default_preprocess_version]
        return RecipeSelection(
            preset_key=None,
            preprocess_version=default_preprocess_version,
            still_max_side=int(default_preprocess["still_max_side"]),
            gif_max_side=int(default_preprocess["gif_max_side"]),
        )

    if recipe_preset not in RECIPE_PRESETS:
        raise ValueError(f"Unknown recipe preset: {recipe_preset}")

    base_recipe = dict(RECIPE_PRESETS[recipe_preset])
    base_preprocess_version = str(base_recipe["preprocess_version"])
    base_preprocess = PREPROCESS_SPECS_BY_VERSION[base_preprocess_version]

    has_custom_preprocess = any(
        value is not None for value in (preprocess_version, still_max_side, gif_max_side)
    )
    if not has_custom_preprocess and recipe_runtime_profile is None:
        return RecipeSelection(
            preset_key=recipe_preset,
            preprocess_version=base_preprocess_version,
            still_max_side=int(base_preprocess["still_max_side"]),
            gif_max_side=int(base_preprocess["gif_max_side"]),
        )

    effective_preprocess_version = preprocess_version or base_preprocess_version
    if still_max_side is None:
        if effective_preprocess_version not in PREPROCESS_SPECS_BY_VERSION:
            raise ValueError(
                "Unknown preprocess version without still_max_side override: "
                f"{effective_preprocess_version}"
            )
        effective_preprocess = PREPROCESS_SPECS_BY_VERSION[effective_preprocess_version]
        effective_still_max_side = int(effective_preprocess["still_max_side"])
        effective_gif_max_side = int(
            effective_preprocess["gif_max_side"] if gif_max_side is None else gif_max_side
        )
        if gif_max_side is not None:
            PREPROCESS_SPECS_BY_VERSION[effective_preprocess_version] = {
                "still_max_side": effective_still_max_side,
                "gif_max_side": effective_gif_max_side,
            }
    else:
        effective_still_max_side = still_max_side
        effective_gif_max_side = int(
            base_preprocess["gif_max_side"] if gif_max_side is None else gif_max_side
        )
        PREPROCESS_SPECS_BY_VERSION[effective_preprocess_version] = {
            "still_max_side": effective_still_max_side,
            "gif_max_side": effective_gif_max_side,
        }

    effective_runtime_profile = (
        recipe_runtime_profile
        or f"{str(base_recipe['runtime_profile'])}-eval-{effective_still_max_side}"
    )
    effective_preset_key = f"{recipe_preset}--{effective_runtime_profile}--{effective_preprocess_version}"
    RECIPE_PRESETS[effective_preset_key] = {
        **base_recipe,
        "runtime_profile": effective_runtime_profile,
        "preprocess_version": effective_preprocess_version,
    }
    return RecipeSelection(
        preset_key=effective_preset_key,
        preprocess_version=effective_preprocess_version,
        still_max_side=effective_still_max_side,
        gif_max_side=effective_gif_max_side,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    wall_started_at = time.perf_counter()

    dataset_dir = Path(args.dataset_dir).resolve()
    labels_path = Path(args.labels_path).resolve()
    labels = json.loads(labels_path.read_text(encoding="utf-8"))

    if args.llama_server:
        os.environ["MEMESORT_LLAMA_SERVER"] = str(Path(args.llama_server).resolve())
    if args.backend == "llama.cpp" and not args.model_name_or_path:
        parser.error("--model-name-or-path is required for llama.cpp")

    recipe_preset = args.recipe_preset
    if args.backend == "llama.cpp" and recipe_preset is None:
        recipe_preset = "qwen3-2b-vulkan-balanced"

    temp_root = Path(tempfile.mkdtemp(prefix="memesort_eval_"))
    library_root = temp_root / "library"
    recipe_selection = prepare_recipe_for_eval(
        recipe_preset=recipe_preset,
        recipe_runtime_profile=args.recipe_runtime_profile,
        preprocess_version=args.preprocess_version,
        still_max_side=args.still_max_side,
        gif_max_side=args.gif_max_side,
    )

    try:
        initialize_library(library_root)
        if recipe_selection.preset_key is not None:
            switch_active_recipe(library_root, recipe_selection.preset_key)

        import_started_at = time.perf_counter()
        import_folder(library_root, dataset_dir)
        import_seconds = time.perf_counter() - import_started_at

        indexing_started_at = time.perf_counter()
        run_pending_jobs(
            library_root,
            backend_name=args.backend,
            model_name_or_path=args.model_name_or_path,
            torch_dtype=args.torch_dtype,
            device=args.device,
            num_threads=args.num_threads,
            num_interop_threads=args.num_interop_threads,
        )
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
                backend_name=args.backend,
                model_name_or_path=args.model_name_or_path,
                torch_dtype=args.torch_dtype,
                device=args.device,
                num_threads=args.num_threads,
                num_interop_threads=args.num_interop_threads,
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
            backend=args.backend,
            model_name_or_path=args.model_name_or_path,
            torch_dtype=args.torch_dtype,
            device=args.device,
            num_threads=args.num_threads,
            num_interop_threads=args.num_interop_threads,
            recipe_preset=recipe_selection.preset_key,
            preprocess_version=recipe_selection.preprocess_version,
            still_max_side=recipe_selection.still_max_side,
            gif_max_side=recipe_selection.gif_max_side,
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
