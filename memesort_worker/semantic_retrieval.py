from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AssetEmbedding:
    asset_id: str
    vector: np.ndarray
    source_ref: str | None
    library_path: str
    media_type: str
    content_hash: str


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes, vector_dim: int) -> np.ndarray:
    vector = np.frombuffer(blob, dtype=np.float32)
    if vector.shape[0] != vector_dim:
        raise ValueError(f"Vector blob dimension mismatch: expected {vector_dim}, got {vector.shape[0]}")
    return vector


def rank_asset_vector_rows(
    query_vector: np.ndarray | list[np.ndarray],
    vector_rows: list[sqlite3.Row],
    top_k: int,
) -> list[dict[str, object]]:
    query_vectors = (
        [query_vector]
        if isinstance(query_vector, np.ndarray)
        else list(query_vector)
    )
    if not query_vectors:
        raise ValueError("At least one query vector is required")

    best_by_asset: dict[str, tuple[float, sqlite3.Row]] = {}
    for row in vector_rows:
        vector = blob_to_vector(bytes(row["vector_blob"]), int(row["vector_dim"]))
        score = max(float(np.dot(candidate, vector)) for candidate in query_vectors)
        asset_id = str(row["asset_id"])
        current = best_by_asset.get(asset_id)
        if current is None or score > current[0]:
            best_by_asset[asset_id] = (score, row)

    scored = list(best_by_asset.values())
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "asset_id": str(row["asset_id"]),
            "score": score,
            "library_path": str(row["library_path"]),
            "library_url": f"/media/{str(row['library_path'])}",
            "thumbnail_url": f"/media/thumbnails/{str(row['asset_id'])}.jpg",
            "media_type": str(row["media_type"]),
            "content_hash": str(row["content_hash"]),
            "matched_source_ref": (
                str(row["source_ref"])
                if "source_ref" in row.keys() and row["source_ref"]
                else None
            ),
        }
        for score, row in scored[:top_k]
    ]


def rank_asset_embeddings(
    query_vector: np.ndarray | list[np.ndarray],
    embeddings: list[AssetEmbedding],
    top_k: int,
) -> list[dict[str, object]]:
    query_vectors = (
        [query_vector]
        if isinstance(query_vector, np.ndarray)
        else list(query_vector)
    )
    if not query_vectors:
        raise ValueError("At least one query vector is required")

    best_by_asset: dict[str, tuple[float, AssetEmbedding]] = {}
    for embedding in embeddings:
        score = max(
            float(np.dot(candidate, embedding.vector))
            for candidate in query_vectors
        )
        current = best_by_asset.get(embedding.asset_id)
        if current is None or score > current[0]:
            best_by_asset[embedding.asset_id] = (score, embedding)

    scored = sorted(best_by_asset.values(), key=lambda item: item[0], reverse=True)
    return [
        {
            "asset_id": embedding.asset_id,
            "score": score,
            "library_path": embedding.library_path,
            "library_url": f"/media/{embedding.library_path}",
            "thumbnail_url": f"/media/thumbnails/{embedding.asset_id}.jpg",
            "media_type": embedding.media_type,
            "content_hash": embedding.content_hash,
            "matched_source_ref": embedding.source_ref,
        }
        for score, embedding in scored[:top_k]
    ]


def scan_duplicate_vector_rows(
    vector_rows: list[sqlite3.Row],
    threshold: float,
) -> list[dict[str, object]]:
    if not vector_rows:
        return []

    vectors_by_asset: dict[str, list[tuple[np.ndarray, sqlite3.Row]]] = {}
    for row in vector_rows:
        asset_id = str(row["asset_id"])
        vectors_by_asset.setdefault(asset_id, []).append(
            (blob_to_vector(bytes(row["vector_blob"]), int(row["vector_dim"])), row)
        )

    asset_ids = sorted(vectors_by_asset.keys())
    pairs: list[dict[str, object]] = []
    for left_index, left_asset_id in enumerate(asset_ids):
        for right_asset_id in asset_ids[left_index + 1 :]:
            best_score = -1.0
            best_left_row = None
            best_right_row = None
            for left_vector, left_row in vectors_by_asset[left_asset_id]:
                for right_vector, right_row in vectors_by_asset[right_asset_id]:
                    score = float(np.dot(left_vector, right_vector))
                    if score > best_score:
                        best_score = score
                        best_left_row = left_row
                        best_right_row = right_row
            if best_score < threshold or best_left_row is None or best_right_row is None:
                continue
            pairs.append(
                {
                    "score": best_score,
                    "asset_a_id": left_asset_id,
                    "asset_b_id": right_asset_id,
                    "asset_a_path": str(best_left_row["library_path"]),
                    "asset_b_path": str(best_right_row["library_path"]),
                    "asset_a_thumbnail_url": f"/media/thumbnails/{left_asset_id}.jpg",
                    "asset_b_thumbnail_url": f"/media/thumbnails/{right_asset_id}.jpg",
                    "asset_a_matched_source_ref": (
                        str(best_left_row["source_ref"]) if best_left_row["source_ref"] else None
                    ),
                    "asset_b_matched_source_ref": (
                        str(best_right_row["source_ref"]) if best_right_row["source_ref"] else None
                    ),
                }
            )
    pairs.sort(key=lambda item: item["score"], reverse=True)
    return pairs
