from __future__ import annotations

import argparse

if __package__:
    from scripts.retrieval_evaluation import DEFAULT_QUERY_FIELDS, run_evaluation
else:
    from retrieval_evaluation import DEFAULT_QUERY_FIELDS, run_evaluation


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
    args = build_parser().parse_args()
    run_evaluation(
        dataset_dir=args.dataset_dir,
        labels_path=args.labels_path,
        query_fields=args.query_fields,
        top_k=args.top_k,
        keep_library=args.keep_library,
        output=args.output,
        temp_prefix="memesort_eval_",
        reject_duplicate_filenames=True,
        include_preprocess=True,
    )


if __name__ == "__main__":
    main()
