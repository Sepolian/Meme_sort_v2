from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from PIL import Image
from paddleocr import PaddleOCR


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
GIF_EXTENSIONS = {".gif"}
DEFAULT_FRAME_COUNT = 4
MIN_LABEL_LINE_LENGTH = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate PaddleOCR against MemeSort example labels.")
    parser.add_argument("--example-dir", default="example", help="Still image directory")
    parser.add_argument("--gif-dir", default="gif_example", help="GIF directory")
    parser.add_argument("--labels-path", default="labels/labels.json", help="Still label JSON")
    parser.add_argument(
        "--output-path",
        default="paddle_ocr_eval.json",
        help="Raw OCR result and recall report path",
    )
    parser.add_argument(
        "--generated-labels-path",
        default="labels/paddle_ocr_generated_labels.json",
        help="Per-image OCR label draft path",
    )
    parser.add_argument("--lang", default="ch", help="PaddleOCR language")
    parser.add_argument("--device", default=None, help="PaddleOCR device, for example gpu:0")
    parser.add_argument("--skip-gif", action="store_true", help="Only evaluate still images")
    parser.add_argument("--gif-frame-count", type=int, default=DEFAULT_FRAME_COUNT)
    parser.add_argument("--fuzzy-threshold", type=float, default=0.82)
    return parser.parse_args()


def normalize_text(text: str) -> str:
    lowered = text.lower()
    return re.sub(r"[\s\W_]+", "", lowered, flags=re.UNICODE)


def label_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(normalize_text(stripped)) >= MIN_LABEL_LINE_LENGTH:
            lines.append(stripped)
    return lines


def fuzzy_contains(expected: str, actual: str, threshold: float) -> tuple[bool, float]:
    expected_norm = normalize_text(expected)
    actual_norm = normalize_text(actual)
    if not expected_norm:
        return False, 0.0
    if expected_norm in actual_norm:
        return True, 1.0

    window = max(len(expected_norm), 1)
    best = 0.0
    if len(actual_norm) <= window:
        best = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    else:
        for start in range(0, len(actual_norm) - window + 1):
            candidate = actual_norm[start : start + window]
            best = max(best, SequenceMatcher(None, expected_norm, candidate).ratio())
    return best >= threshold, best


def image_paths(directory: Path, extensions: set[str]) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    )


def gif_frame_indices(path: Path, frame_count: int) -> list[int]:
    with Image.open(path) as image:
        total = getattr(image, "n_frames", 1)
    if total <= 1:
        return [0]
    count = max(1, min(frame_count, total))
    if count == 1:
        return [0]
    return sorted({round(index * (total - 1) / (count - 1)) for index in range(count)})


def extract_gif_frame(path: Path, frame_index: int, output_path: Path) -> None:
    with Image.open(path) as image:
        image.seek(frame_index)
        image.convert("RGB").save(output_path)


def predict_path(ocr: PaddleOCR, path: Path) -> dict[str, object]:
    started_at = time.perf_counter()
    result = ocr.predict(str(path.resolve()))
    elapsed = time.perf_counter() - started_at
    if not result:
        return {
            "texts": [],
            "scores": [],
            "boxes": [],
            "text": "",
            "seconds": round(elapsed, 3),
        }
    item = result[0]
    texts = [str(text) for text in item.get("rec_texts", [])]
    scores = [float(score) for score in item.get("rec_scores", [])]
    boxes = []
    for box in item.get("rec_boxes", []):
        try:
            boxes.append([int(value) for value in box])
        except TypeError:
            boxes.append([])
    return {
        "texts": texts,
        "scores": scores,
        "boxes": boxes,
        "text": "\n".join(texts),
        "seconds": round(elapsed, 3),
    }


def merge_texts(results: Iterable[dict[str, object]]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for result in results:
        for text in result.get("texts", []):
            text_value = str(text).strip()
            key = normalize_text(text_value)
            if text_value and key not in seen:
                seen.add(key)
                lines.append(text_value)
    return "\n".join(lines)


def evaluate_still_recall(
    ocr_by_filename: dict[str, dict[str, object]],
    labels: dict[str, dict[str, object]],
    fuzzy_threshold: float,
) -> dict[str, object]:
    per_asset = []
    total_lines = 0
    hit_lines = 0
    labeled_assets = 0
    assets_with_any_hit = 0

    for filename, payload in sorted(labels.items()):
        expected_text = str(payload.get("ocr_text") or "").strip()
        if not expected_text:
            continue
        expected_lines = label_lines(expected_text)
        if not expected_lines:
            continue
        labeled_assets += 1
        actual_text = str(ocr_by_filename.get(filename, {}).get("text") or "")
        line_results = []
        asset_hits = 0
        for expected in expected_lines:
            total_lines += 1
            matched, score = fuzzy_contains(expected, actual_text, fuzzy_threshold)
            if matched:
                hit_lines += 1
                asset_hits += 1
            line_results.append(
                {
                    "expected": expected,
                    "matched": matched,
                    "best_similarity": round(score, 3),
                }
            )
        if asset_hits:
            assets_with_any_hit += 1
        per_asset.append(
            {
                "filename": filename,
                "expected_line_count": len(expected_lines),
                "matched_line_count": asset_hits,
                "line_recall": round(asset_hits / len(expected_lines), 3),
                "actual_text": actual_text,
                "lines": line_results,
            }
        )

    return {
        "labeled_assets": labeled_assets,
        "assets_with_any_hit": assets_with_any_hit,
        "asset_any_hit_recall": round(assets_with_any_hit / labeled_assets, 3) if labeled_assets else 0.0,
        "expected_line_count": total_lines,
        "matched_line_count": hit_lines,
        "line_recall": round(hit_lines / total_lines, 3) if total_lines else 0.0,
        "fuzzy_threshold": fuzzy_threshold,
        "per_asset": per_asset,
    }


def main() -> None:
    args = parse_args()
    example_dir = Path(args.example_dir)
    gif_dir = Path(args.gif_dir)
    labels_path = Path(args.labels_path)
    output_path = Path(args.output_path)
    generated_labels_path = Path(args.generated_labels_path)

    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    ocr_kwargs = {"lang": args.lang}
    if args.device:
        ocr_kwargs["device"] = args.device
    ocr = PaddleOCR(**ocr_kwargs)

    still_results: dict[str, dict[str, object]] = {}
    for path in image_paths(example_dir, IMAGE_EXTENSIONS):
        still_results[path.name] = predict_path(ocr, path)

    gif_results: dict[str, dict[str, object]] = {}
    if not args.skip_gif:
        with tempfile.TemporaryDirectory(prefix="memesort-paddle-ocr-") as temp_dir:
            temp_root = Path(temp_dir)
            for path in image_paths(gif_dir, GIF_EXTENSIONS):
                frame_results = []
                for frame_index in gif_frame_indices(path, args.gif_frame_count):
                    frame_path = temp_root / f"{path.stem}-{frame_index}.jpg"
                    extract_gif_frame(path, frame_index, frame_path)
                    frame_result = predict_path(ocr, frame_path)
                    frame_result["frame_index"] = frame_index
                    frame_results.append(frame_result)
                gif_results[path.name] = {
                    "frames": frame_results,
                    "text": merge_texts(frame_results),
                }

    recall = evaluate_still_recall(still_results, labels, args.fuzzy_threshold)
    generated_labels = {
        filename: {
            "ocr_text": str(result.get("text") or ""),
            "ocr_lines": result.get("texts", []),
            "ocr_scores": result.get("scores", []),
            "source": "paddleocr-3.6.0+paddlepaddle-3.2.2-cpu",
        }
        for filename, result in sorted(still_results.items())
    }
    for filename, result in sorted(gif_results.items()):
        generated_labels[filename] = {
            "ocr_text": str(result.get("text") or ""),
            "ocr_lines": [
                line
                for frame in result.get("frames", [])
                for line in frame.get("texts", [])
            ],
            "frames": result.get("frames", []),
            "source": "paddleocr-3.6.0+paddlepaddle-3.2.2-cpu",
        }

    report = {
        "engine": "paddleocr",
        "paddleocr_version": "3.6.0",
        "paddlepaddle_version": "3.2.2",
        "lang": args.lang,
        "still_image_count": len(still_results),
        "gif_count": len(gif_results),
        "recall_against_existing_ocr_text": recall,
        "still_results": still_results,
        "gif_results": gif_results,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    generated_labels_path.write_text(
        json.dumps(generated_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "output_path": str(output_path.resolve()),
            "generated_labels_path": str(generated_labels_path.resolve()),
            "still_image_count": len(still_results),
            "gif_count": len(gif_results),
            "line_recall": recall["line_recall"],
            "asset_any_hit_recall": recall["asset_any_hit_recall"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
