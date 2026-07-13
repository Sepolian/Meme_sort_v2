from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path


def configure_jsonl_stdio() -> None:
    """Keep the worker protocol UTF-8 regardless of the Windows code page."""
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JSONL PaddleOCR worker for MemeSort.")
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def load_ocr(lang: str, device: str):
    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCR

        options = {
            "device": device,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        if lang.lower() == "ch":
            options.update(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
            )
        else:
            options["lang"] = lang
        return PaddleOCR(**options)


def predict_path(ocr, path: Path) -> dict[str, object]:
    started_at = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        result = ocr.predict(str(path.resolve()))
    elapsed = time.perf_counter() - started_at
    if not result:
        return {
            "engine": "paddleocr-worker",
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
        "engine": "paddleocr-worker",
        "texts": texts,
        "scores": scores,
        "boxes": boxes,
        "text": "\n".join(texts),
        "seconds": round(elapsed, 3),
    }


def main() -> int:
    configure_jsonl_stdio()
    args = parse_args()
    try:
        ocr = load_ocr(args.lang, args.device)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "id": None,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1

    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            request = json.loads(stripped)
            request_id = request.get("id")
            path = Path(str(request["path"]))
            payload = {
                "id": request_id,
                "result": predict_path(ocr, path),
            }
        except Exception as exc:
            payload = {
                "id": request.get("id") if "request" in locals() else None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
