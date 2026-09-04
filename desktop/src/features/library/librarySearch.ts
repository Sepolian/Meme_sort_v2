import type { AssetSummary } from "../../api/types";
import { getAssetDisplayName } from "./libraryOrdering";

/**
 * Local Library filtering (ticket 11).
 *
 * Typing filters the loaded `AssetSummary` list by displayed Asset name and
 * the available/primary Source Path only. Matching is case-insensitive
 * substring matching. Pending and Failed Assets are included (no status
 * filtering here); semantic retrieval is the only mode that excludes
 * non-Indexed Assets.
 *
 * Documented data only:
 * - displayed name via `getAssetDisplayName` (first Source Record basename,
 *   else `library_path` basename, else `asset_id`);
 * - available/primary Source Path via `source_records[0]?.source_path`.
 * Callers must not claim all historical paths.
 */
export function matchesLocalQuery(asset: AssetSummary, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const displayName = getAssetDisplayName(asset).toLowerCase();
  if (displayName.includes(needle)) return true;
  const primarySource = (asset.source_records[0]?.source_path ?? "").toLowerCase();
  if (primarySource && primarySource.includes(needle)) return true;
  return false;
}

/**
 * Filter Assets for local mode, preserving input order (callers pass the
 * already sorted/filtered waterfall input so sort order is retained).
 */
export function filterLocalAssets(
  assets: readonly AssetSummary[],
  query: string,
): AssetSummary[] {
  if (query.trim() === "") return [...assets];
  return assets.filter((asset) => matchesLocalQuery(asset, query));
}
