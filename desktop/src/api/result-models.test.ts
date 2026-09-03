import { describe, expect, it } from "vitest";
import type { AssetSummary, DuplicatePair, SearchAsset } from "./types";
import {
  buildAssetSummaryMap,
  composeDuplicatePairs,
  composeSearchItems,
} from "./result-models";

function summary(overrides: Partial<AssetSummary> & { asset_id: string }): AssetSummary {
  return {
    library_path: `originals/${overrides.asset_id.slice(-4)}.png`,
    library_url: `/media/originals/${overrides.asset_id.slice(-4)}.png`,
    thumbnail_url: `/media/thumbnails/${overrides.asset_id.slice(-4)}.jpg`,
    media_type: "image/png",
    content_hash: `hash-${overrides.asset_id.slice(-4)}`,
    width: 320,
    height: 180,
    imported_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
    source_record_count: 2,
    source_records: [{ source_path: `C:/Source/${overrides.asset_id.slice(-4)}.png` }],
    status: "indexed",
    ...overrides,
  };
}

function searchAsset(overrides: Partial<SearchAsset> & { asset_id: string }): SearchAsset {
  return {
    library_url: "/media/originals/stale-projection.png",
    thumbnail_url: "/media/thumbnails/stale-projection.jpg",
    library_path: "originals/stale-projection.png",
    media_type: "image/png",
    score: 0.5,
    match_sources: ["visual"],
    ocr_snippet: null,
    ...overrides,
  };
}

function duplicatePair(overrides: Partial<DuplicatePair>): DuplicatePair {
  return {
    score: 0.97,
    asset_a_id: "123e4567-e89b-12d3-a456-426614174000",
    asset_b_id: "123e4567-e89b-12d3-a456-426614174001",
    asset_a_path: "originals/stale-a.png",
    asset_b_path: "originals/stale-b.png",
    asset_a_thumbnail_url: "/media/thumbnails/stale-a.jpg",
    asset_b_thumbnail_url: "/media/thumbnails/stale-b.jpg",
    asset_a_matched_source_ref: "frame:2",
    asset_b_matched_source_ref: null,
    ...overrides,
  };
}

describe("composeSearchItems", () => {
  it("joins each SearchAsset to its AssetSummary while preserving relevance order and scores", () => {
    const firstId = "123e4567-e89b-12d3-a456-426614174000";
    const secondId = "123e4567-e89b-12d3-a456-426614174001";
    const summaries = buildAssetSummaryMap([
      summary({ asset_id: firstId, width: 640, height: 360, status: "indexed" }),
      summary({ asset_id: secondId, width: 100, height: 200, status: "indexed" }),
    ]);
    const results = [
      searchAsset({ asset_id: secondId, score: 0.91, match_sources: ["visual"], ocr_snippet: "second" }),
      searchAsset({ asset_id: firstId, score: 0.84, match_sources: ["visual", "ocr"], ocr_snippet: null }),
    ];

    const composed = composeSearchItems(results, summaries);

    expect(composed.stale).toEqual([]);
    expect(composed.items.map((item) => item.summary.asset_id)).toEqual([secondId, firstId]);
    expect(composed.items[0].score).toBe(0.91);
    expect(composed.items[0].matchSources).toEqual(["visual"]);
    expect(composed.items[0].ocrSnippet).toBe("second");
    expect(composed.items[1].score).toBe(0.84);
    expect(composed.items[1].matchSources).toEqual(["visual", "ocr"]);
    // Dimensions, status, timestamps, and Source Records come from the summary.
    expect(composed.items[0].summary.width).toBe(100);
    expect(composed.items[0].summary.height).toBe(200);
    expect(composed.items[0].summary.status).toBe("indexed");
    expect(composed.items[0].summary.source_record_count).toBe(2);
    expect(composed.items[1].summary.width).toBe(640);
  });

  it("omits missing summaries with a typed stale-result diagnostic instead of invented dimensions", () => {
    const knownId = "123e4567-e89b-12d3-a456-426614174000";
    const missingId = "123e4567-e89b-12d3-a456-426614174099";
    const summaries = buildAssetSummaryMap([summary({ asset_id: knownId })]);
    const results = [
      searchAsset({ asset_id: knownId, score: 0.9 }),
      searchAsset({ asset_id: missingId, score: 0.8 }),
    ];

    const composed = composeSearchItems(results, summaries);

    expect(composed.items.map((item) => item.summary.asset_id)).toEqual([knownId]);
    expect(composed.stale).toEqual([{ assetId: missingId, reason: "missing-summary" }]);
  });

  it("returns empty items and stale lists for empty input", () => {
    const composed = composeSearchItems([], new Map());

    expect(composed.items).toEqual([]);
    expect(composed.stale).toEqual([]);
  });
});

describe("composeDuplicatePairs", () => {
  it("joins both Asset IDs to complete summaries while preserving score and Matched Frames", () => {
    const assetAId = "123e4567-e89b-12d3-a456-426614174000";
    const assetBId = "123e4567-e89b-12d3-a456-426614174001";
    const summaries = buildAssetSummaryMap([
      summary({ asset_id: assetAId, width: 640, height: 360, source_record_count: 3 }),
      summary({ asset_id: assetBId, width: 100, height: 100, source_record_count: 1 }),
    ]);
    const pairs = [
      duplicatePair({
        score: 0.973,
        asset_a_id: assetAId,
        asset_b_id: assetBId,
        asset_a_matched_source_ref: "frame:2",
        asset_b_matched_source_ref: "frame:7",
      }),
    ];

    const composed = composeDuplicatePairs(pairs, summaries);

    expect(composed.stale).toEqual([]);
    expect(composed.pairs).toHaveLength(1);
    const [pair] = composed.pairs;
    expect(pair.score).toBe(0.973);
    expect(pair.assetA.asset_id).toBe(assetAId);
    expect(pair.assetB.asset_id).toBe(assetBId);
    expect(pair.assetAMatchedSourceRef).toBe("frame:2");
    expect(pair.assetBMatchedSourceRef).toBe("frame:7");
    // Dimensions and Source Record counts come from summaries, not projected paths.
    expect(pair.assetA.width).toBe(640);
    expect(pair.assetA.source_record_count).toBe(3);
    expect(pair.assetB.width).toBe(100);
    expect(pair.assetB.source_record_count).toBe(1);
  });

  it("preserves pair ordering for complete pairs", () => {
    const ids = [
      "123e4567-e89b-12d3-a456-426614174000",
      "123e4567-e89b-12d3-a456-426614174001",
      "123e4567-e89b-12d3-a456-426614174002",
    ];
    const summaries = buildAssetSummaryMap(ids.map((asset_id) => summary({ asset_id })));
    const pairs = [
      duplicatePair({ asset_a_id: ids[1], asset_b_id: ids[2], score: 0.99 }),
      duplicatePair({ asset_a_id: ids[0], asset_b_id: ids[1], score: 0.95 }),
    ];

    const composed = composeDuplicatePairs(pairs, summaries);

    expect(composed.pairs.map((pair) => pair.score)).toEqual([0.99, 0.95]);
    expect(composed.stale).toEqual([]);
  });

  it("omits pairs with a missing side and reports which IDs are stale", () => {
    const assetAId = "123e4567-e89b-12d3-a456-426614174000";
    const missingBId = "123e4567-e89b-12d3-a456-426614174099";
    const summaries = buildAssetSummaryMap([summary({ asset_id: assetAId })]);
    const pairs = [
      duplicatePair({ asset_a_id: assetAId, asset_b_id: missingBId, score: 0.96 }),
    ];

    const composed = composeDuplicatePairs(pairs, summaries);

    expect(composed.pairs).toEqual([]);
    expect(composed.stale).toEqual([
      { assetAId, assetBId: missingBId, missingIds: [missingBId] },
    ]);
  });

  it("reports both IDs when neither side has a loaded summary", () => {
    const summaries = buildAssetSummaryMap([]);
    const pairs = [
      duplicatePair({
        asset_a_id: "123e4567-e89b-12d3-a456-426614174010",
        asset_b_id: "123e4567-e89b-12d3-a456-426614174011",
      }),
    ];

    const composed = composeDuplicatePairs(pairs, summaries);

    expect(composed.pairs).toEqual([]);
    expect(composed.stale).toHaveLength(1);
    expect(composed.stale[0].missingIds).toEqual([
      "123e4567-e89b-12d3-a456-426614174010",
      "123e4567-e89b-12d3-a456-426614174011",
    ]);
  });
});

describe("buildAssetSummaryMap", () => {
  it("indexes summaries by Asset ID for join lookups", () => {
    const firstId = "123e4567-e89b-12d3-a456-426614174000";
    const secondId = "123e4567-e89b-12d3-a456-426614174001";
    const map = buildAssetSummaryMap([
      summary({ asset_id: firstId }),
      summary({ asset_id: secondId }),
    ]);

    expect(map.get(firstId)?.asset_id).toBe(firstId);
    expect(map.get(secondId)?.asset_id).toBe(secondId);
    expect(map.size).toBe(2);
  });
});
