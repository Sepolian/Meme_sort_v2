from __future__ import annotations

import argparse

if __package__:
    from scripts.retrieval_evaluation import build_evaluation_parser, run_evaluation
else:
    from retrieval_evaluation import build_evaluation_parser, run_evaluation


def build_parser() -> argparse.ArgumentParser:
    return build_evaluation_parser(
        media_type="GIF",
        dataset_dir="gif_example",
        labels_path="labels/gif_labels.json",
    )


def main() -> None:
    args = build_parser().parse_args()
    run_evaluation(
        dataset_dir=args.dataset_dir,
        labels_path=args.labels_path,
        query_fields=args.query_fields,
        top_k=args.top_k,
        keep_library=args.keep_library,
        output=args.output,
        temp_prefix="memesort_gif_eval_",
        reject_duplicate_filenames=False,
        include_preprocess=False,
    )


if __name__ == "__main__":
    main()
