import type { AssetSummary, DuplicatePair, SearchAsset } from "./types";

/**
 * Shared result composition for ticket 05.
 *
 * Retrieval projections (`SearchAsset`, `DuplicatePair`) never carry complete
 * card/comparison data. Dimensions, status, timestamps, and Source Record
 * summaries always come from the loaded `AssetSummary` map; scores and
 * Matched Frame references stay on the retrieval projection.
 *
 * Missing joins are omitted with a typed stale-result diagnostic. Callers
 * render recoverable feedback from `stale` and must not invent dimensions
 * or Source Records for missing Assets.
 */

export interface ComposedSearchItem {
  /** Complete card model sourced from the loaded Asset list. */
  summary: AssetSummary;
  /** Relevance score preserved from the retrieval projection. */
  score: number;
  /** Retrieval match sources preserved from the projection. */
  matchSources: string[];
  /** OCR snippet preserved from the projection, if any. */
  ocrSnippet: string | null;
}

export interface StaleSearchResult {
  assetId: string;
  reason: "missing-summary";
}

export interface ComposedSearchResults {
  items: ComposedSearchItem[];
  stale: StaleSearchResult[];
}

export interface ComposedDuplicatePair {
  /** Similarity score preserved from the DuplicatePair projection. */
  score: number;
  /** Complete left comparison item from the AssetSummary map. */
  assetA: AssetSummary;
  /** Complete right comparison item from the AssetSummary map. */
  assetB: AssetSummary;
  /** Matched Frame reference preserved from the projection. */
  assetAMatchedSourceRef: string | null;
  /** Matched Frame reference preserved from the projection. */
  assetBMatchedSourceRef: string | null;
}

export interface StaleDuplicatePair {
  assetAId: string;
  assetBId: string;
  missingIds: string[];
}

export interface ComposedDuplicateResults {
  pairs: ComposedDuplicatePair[];
  stale: StaleDuplicatePair[];
}

export function buildAssetSummaryMap(assets: readonly AssetSummary[]): Map<string, AssetSummary> {
  const map = new Map<string, AssetSummary>();
  for (const asset of assets) {
    map.set(asset.asset_id, asset);
  }
  return map;
}

export function composeSearchItems(
  results: readonly SearchAsset[],
  summaries: ReadonlyMap<string, AssetSummary>,
): ComposedSearchResults {
  const items: ComposedSearchItem[] = [];
  const stale: StaleSearchResult[] = [];
  for (const result of results) {
    const summary = summaries.get(result.asset_id);
    if (!summary) {
      stale.push({ assetId: result.asset_id, reason: "missing-summary" });
      continue;
    }
    items.push({
      summary,
      score: result.score,
      matchSources: [...result.match_sources],
      ocrSnippet: result.ocr_snippet ?? null,
    });
  }
  return { items, stale };
}

export function composeDuplicatePairs(
  pairs: readonly DuplicatePair[],
  summaries: ReadonlyMap<string, AssetSummary>,
): ComposedDuplicateResults {
  const composed: ComposedDuplicatePair[] = [];
  const stale: StaleDuplicatePair[] = [];
  for (const pair of pairs) {
    const assetA = summaries.get(pair.asset_a_id);
    const assetB = summaries.get(pair.asset_b_id);
    if (!assetA || !assetB) {
      const missingIds: string[] = [];
      if (!assetA) missingIds.push(pair.asset_a_id);
      if (!assetB) missingIds.push(pair.asset_b_id);
      stale.push({ assetAId: pair.asset_a_id, assetBId: pair.asset_b_id, missingIds });
      continue;
    }
    composed.push({
      score: pair.score,
      assetA,
      assetB,
      assetAMatchedSourceRef: pair.asset_a_matched_source_ref,
      assetBMatchedSourceRef: pair.asset_b_matched_source_ref,
    });
  }
  return { pairs: composed, stale };
}
