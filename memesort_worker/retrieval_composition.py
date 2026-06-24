from __future__ import annotations

RRF_K = 60
VISUAL_SOURCE = "visual"
OCR_SOURCE = "ocr"


def compose_text_search_results(
    visual_results: list[dict[str, object]],
    ocr_results: list[dict[str, object]],
    top_k: int,
) -> list[dict[str, object]]:
    fused: dict[str, dict[str, object]] = {}

    for rank, result in enumerate(visual_results, start=1):
        asset_id = str(result["asset_id"])
        payload = _ensure_result(fused, asset_id, result)
        _add_rrf_score(payload, rank)
        payload["visual_score"] = float(result.get("score", 0.0))
        _add_match_source(payload, VISUAL_SOURCE)

    for rank, result in enumerate(ocr_results, start=1):
        asset_id = str(result["asset_id"])
        payload = _ensure_result(fused, asset_id, result)
        _add_rrf_score(payload, rank)
        _copy_ocr_explanation(payload, result)
        _add_match_source(payload, OCR_SOURCE)

    results = list(fused.values())
    results.sort(key=lambda item: float(item["score"]), reverse=True)
    return results[:top_k]


def _ensure_result(
    fused: dict[str, dict[str, object]],
    asset_id: str,
    source: dict[str, object],
) -> dict[str, object]:
    if asset_id not in fused:
        payload = dict(source)
        payload["score"] = 0.0
        payload["match_sources"] = []
        fused[asset_id] = payload
    return fused[asset_id]


def _add_rrf_score(payload: dict[str, object], rank: int) -> None:
    payload["score"] = float(payload["score"]) + 1.0 / (RRF_K + rank)


def _copy_ocr_explanation(
    payload: dict[str, object],
    ocr_result: dict[str, object],
) -> None:
    payload["ocr_score"] = ocr_result.get("ocr_score")
    payload["ocr_confidence"] = ocr_result.get("ocr_confidence")
    payload["ocr_snippet"] = ocr_result.get("ocr_snippet")
    payload["ocr_text"] = ocr_result.get("ocr_text")


def _add_match_source(payload: dict[str, object], source: str) -> None:
    match_sources = payload["match_sources"]
    if source not in match_sources:
        match_sources.append(source)
