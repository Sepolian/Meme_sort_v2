/**
 * Deterministic waterfall layout helpers (ticket 09).
 *
 * The Library waterfall consumes the already sorted input from ticket 08's
 * `getOrderedLibraryAssets` in order and places each next item in the
 * currently shortest column with deterministic leftmost tie-breaking. Column
 * heights are estimated from the reserved aspect ratio (normalized width 1),
 * so placement is stable before any media loads and never changes when a
 * thumbnail completes.
 *
 * Explicitly out of scope here:
 * - CSS multicol is not used: its visual traversal (down one column, then the
 *   next) would reorder the sorted input. Explicit column containers keep each
 *   column's DOM subsequence in input order, with the earliest items weighted
 *   toward the top of the wall.
 * - Experimental WebView2 CSS masonry is not required; this module is plain
 *   TypeScript with no browser-only APIs, so it unit-tests in Node/jsdom.
 *
 * Aspect-ratio contract:
 * - `AssetSummary.width/height` reserve each card's geometry before load.
 * - Missing/invalid dimensions (null, zero, negative, NaN, Infinity) use the
 *   documented 1:1 fallback (`WATERFALL_FALLBACK_ASPECT_RATIO`), so every card
 *   reserves identical geometry to a same-ratio card and load completion never
 *   relocates placed cards.
 *
 * Density geometry (spec ticket 18 direction):
 * - comfortable: 220px minimum column width, 12px gap.
 * - compact: 168px minimum column width, 8px gap.
 */

import type { AssetSummary } from "../../api/types";
import type { LibraryDensity } from "../library/libraryUrlState";

/**
 * Documented 1:1 fallback aspect ratio (width / height) used when an Asset
 * has missing or invalid dimensions.
 */
export const WATERFALL_FALLBACK_ASPECT_RATIO = 1;

/**
 * Near-viewport preloading margin for the lazy media observer. Media whose
 * card is outside this margin around the viewport is never requested; the
 * card keeps its reserved placeholder instead of an `<img src>`.
 */
export const WATERFALL_LAZY_ROOT_MARGIN = "400px 0px";

/** Minimum column width and gap per persisted density. */
export const WATERFALL_GEOMETRY = {
  comfortable: { minColumnWidth: 220, gap: 12 },
  compact: { minColumnWidth: 168, gap: 8 },
} as const;

function isPositiveFiniteDimension(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

type DimensionsLike = Pick<AssetSummary, "width" | "height">;

/**
 * Width-over-height ratio for aspect-ratio reservation. Returns
 * `WATERFALL_FALLBACK_ASPECT_RATIO` (1:1) for missing/invalid dimensions.
 */
export function getAssetAspectRatio(asset: DimensionsLike): number {
  if (
    isPositiveFiniteDimension(asset.width) &&
    isPositiveFiniteDimension(asset.height)
  ) {
    return asset.width / asset.height;
  }
  return WATERFALL_FALLBACK_ASPECT_RATIO;
}

/**
 * Inline `aspect-ratio` style value for a card's media wrapper. Valid
 * dimensions keep their exact ratio (`"320 / 180"`); anything else resolves
 * to the same `"1 / 1"` fallback so geometry stays uniform and stable.
 */
export function getAssetAspectRatioStyle(asset: DimensionsLike): {
  aspectRatio: string;
} {
  if (
    isPositiveFiniteDimension(asset.width) &&
    isPositiveFiniteDimension(asset.height)
  ) {
    return { aspectRatio: `${asset.width} / ${asset.height}` };
  }
  return { aspectRatio: "1 / 1" };
}

/**
 * Normalized column-height contribution of one item (assumes column width 1).
 * Card chrome outside the media box is constant per card (the hover overlay
 * always occupies layout space via opacity, never `display: none`), so the
 * reserved media height dominates balancing.
 */
export function estimateWaterfallItemHeight(asset: DimensionsLike): number {
  return 1 / getAssetAspectRatio(asset);
}

/**
 * Column count for a measured container width under a density. Always at
 * least 1; non-finite/non-positive widths (unmeasurable containers, tests)
 * yield a single column so DOM order trivially preserves input order.
 */
export function getWaterfallColumnCount(
  containerWidth: number,
  density: LibraryDensity,
): number {
  const geometry =
    WATERFALL_GEOMETRY[density] ?? WATERFALL_GEOMETRY.comfortable;
  if (!Number.isFinite(containerWidth) || containerWidth <= 0) return 1;
  return Math.max(
    1,
    Math.floor(
      (containerWidth + geometry.gap) /
        (geometry.minColumnWidth + geometry.gap),
    ),
  );
}

/**
 * Deterministic shortest-column placement consuming `items` in order.
 *
 * Each item goes to the currently shortest column; ties resolve to the
 * leftmost (lowest-index) column via strict `<` comparison, so repeated runs
 * over the same input always produce the same columns. Non-finite or negative
 * heights are treated as 0 rather than corrupting the running totals.
 */
export function assignWaterfallColumns<T>(
  items: readonly T[],
  columnCount: number,
  getHeight: (item: T, index: number) => number,
): T[][] {
  const count =
    Number.isFinite(columnCount) && columnCount >= 1
      ? Math.floor(columnCount)
      : 1;
  const columns: T[][] = Array.from({ length: count }, () => []);
  const heights = new Array<number>(count).fill(0);
  items.forEach((item, index) => {
    let target = 0;
    for (let column = 1; column < count; column += 1) {
      if (heights[column] < heights[target]) target = column;
    }
    columns[target].push(item);
    const height = getHeight(item, index);
    heights[target] +=
      Number.isFinite(height) && (height as number) >= 0 ? height : 0;
  });
  return columns;
}
