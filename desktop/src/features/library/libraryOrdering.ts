/**
 * Library sorting, filtering, and density ordering (ticket 08).
 *
 * Owns all non-search Library controls' list semantics and produces one
 * stable, ordered Asset list for the waterfall (ticket 09 consumes this
 * output in order).
 *
 * Contract:
 * - Filtering composes with AND semantics: media AND status must both pass.
 * - Sorting is applied AFTER filtering.
 * - State flows only through ticket 07's URL/preference contract
 *   (`libraryUrlState` + `useLibraryUrlState`); this module is pure and never
 *   touches URL or storage directly.
 * - `newest` is the default sort (see `DEFAULT_LIBRARY_SORT` in
 *   `libraryUrlState`).
 *
 * Sort behaviors (all deterministic, locale-stable, ending in Asset ID):
 * - `newest`: `imported_at` descending (newest first). Unparsable/missing
 *   timestamps are treated as epoch 0 so they sort last.
 *   Tie breakers: display name (locale, numeric) -> media_type (locale) ->
 *   status (locale) -> asset_id (code-unit ascending).
 * - `oldest`: `imported_at` ascending (oldest first). Unparsable/missing
 *   timestamps are treated as epoch 0 so they sort first.
 *   Tie breakers: same as `newest`.
 * - `name`: display name ascending with a locale collator
 *   (`numeric: true, sensitivity: "base"`), so "file2" sorts before "file10"
 *   and case differences do not reorder. Missing names fall back to the
 *   Asset ID (see `getAssetDisplayName`), so nameless Assets sort by ID.
 *   Tie breakers: `imported_at` descending -> media_type (locale) ->
 *   status (locale) -> asset_id (code-unit ascending).
 * - `type`: `media_type` ascending (locale compare, e.g. "image/gif" <
 *   "image/png"). Empty/missing types sort first.
 *   Tie breakers: display name (locale, numeric) -> `imported_at`
 *   descending -> status (locale) -> asset_id (code-unit ascending).
 * - `status`: `status` string ascending (locale compare, so alphabetical:
 *   "failed" < "indexed" < "pending"; unknown statuses slot alphabetically).
 *   Tie breakers: display name (locale, numeric) -> `imported_at`
 *   descending -> media_type (locale) -> asset_id (code-unit ascending).
 *
 * The final tie breaker is always `asset_id` with code-unit (`<`/`>`)
 * comparison, which is locale-independent and guarantees a total order even
 * when the locale collator reports ties. Combined with modern stable
 * `Array.prototype.sort`, card placement never changes nondeterministically.
 *
 * Display name (`getAssetDisplayName`):
 * - basename of the first Source Record's `source_path` when it yields a
 *   non-empty segment, else basename of `library_path`, else the `asset_id`.
 * - Both Windows (`\`) and POSIX (`/`) separators are supported.
 */

import type { AssetSummary } from "../../api/types";
import type {
  LibraryMediaFilter,
  LibrarySort,
  LibraryStatusFilter,
} from "./libraryUrlState";

export interface LibraryFilterOptions {
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
}

export interface LibraryOrderingOptions extends LibraryFilterOptions {
  sort: LibrarySort;
}

const nameCollator =
  typeof Intl !== "undefined" && typeof Intl.Collator === "function"
    ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
    : null;

const genericCollator =
  typeof Intl !== "undefined" && typeof Intl.Collator === "function"
    ? new Intl.Collator(undefined, { sensitivity: "base" })
    : null;

function compareLocale(a: string, b: string): number {
  if (genericCollator) return genericCollator.compare(a, b);
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function compareName(a: string, b: string): number {
  if (nameCollator) {
    const result = nameCollator.compare(a, b);
    if (result !== 0) return result;
  } else if (a !== b) {
    return a < b ? -1 : 1;
  }
  // Deterministic code-unit fallback when the locale collator ties, so
  // ordering does not depend on collator internals.
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/** Locale-independent total-order comparison for Asset IDs. */
function compareAssetId(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function basename(path: string | null | undefined): string {
  if (!path) return "";
  const segments = path.split(/[\\/]/);
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index]?.trim() ?? "";
    if (segment) return segment;
  }
  return "";
}

/**
 * Display name used for the `name` sort and as a tie breaker everywhere.
 * Falls back to `asset_id` when neither Source Records nor `library_path`
 * yield a basename, so missing names still sort deterministically.
 */
export function getAssetDisplayName(asset: AssetSummary): string {
  const fromSource = basename(asset.source_records[0]?.source_path);
  if (fromSource) return fromSource;
  const fromLibrary = basename(asset.library_path);
  if (fromLibrary) return fromLibrary;
  return asset.asset_id;
}

/** A GIF Asset has an `image/gif` media type (case-insensitive). */
export function isGifAsset(asset: AssetSummary): boolean {
  const mediaType = (asset.media_type ?? "").trim().toLowerCase();
  return mediaType === "image/gif" || mediaType.endsWith("/gif");
}

function parseImportedAt(importedAt: string | null | undefined): number {
  if (!importedAt) return 0;
  const parsed = Date.parse(importedAt);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Filter Assets with AND semantics. `all` disables that dimension so the two
 * filters compose rather than replacing each other.
 */
export function filterLibraryAssets(
  assets: readonly AssetSummary[],
  filters: LibraryFilterOptions,
): AssetSummary[] {
  const { media, status } = filters;
  if (media === "all" && status === "all") return [...assets];
  return assets.filter((asset) => {
    if (media === "gif" && !isGifAsset(asset)) return false;
    if (media === "still" && isGifAsset(asset)) return false;
    if (status !== "all" && asset.status !== status) return false;
    return true;
  });
}

function compareNewest(a: AssetSummary, b: AssetSummary): number {
  const timeDiff = parseImportedAt(b.imported_at) - parseImportedAt(a.imported_at);
  if (timeDiff !== 0) return timeDiff;
  const nameDiff = compareName(getAssetDisplayName(a), getAssetDisplayName(b));
  if (nameDiff !== 0) return nameDiff;
  const typeDiff = compareLocale(a.media_type ?? "", b.media_type ?? "");
  if (typeDiff !== 0) return typeDiff;
  const statusDiff = compareLocale(a.status ?? "", b.status ?? "");
  if (statusDiff !== 0) return statusDiff;
  return compareAssetId(a.asset_id, b.asset_id);
}

function compareOldest(a: AssetSummary, b: AssetSummary): number {
  const timeDiff = parseImportedAt(a.imported_at) - parseImportedAt(b.imported_at);
  if (timeDiff !== 0) return timeDiff;
  const nameDiff = compareName(getAssetDisplayName(a), getAssetDisplayName(b));
  if (nameDiff !== 0) return nameDiff;
  const typeDiff = compareLocale(a.media_type ?? "", b.media_type ?? "");
  if (typeDiff !== 0) return typeDiff;
  const statusDiff = compareLocale(a.status ?? "", b.status ?? "");
  if (statusDiff !== 0) return statusDiff;
  return compareAssetId(a.asset_id, b.asset_id);
}

function compareByName(a: AssetSummary, b: AssetSummary): number {
  const nameDiff = compareName(getAssetDisplayName(a), getAssetDisplayName(b));
  if (nameDiff !== 0) return nameDiff;
  const timeDiff = parseImportedAt(b.imported_at) - parseImportedAt(a.imported_at);
  if (timeDiff !== 0) return timeDiff;
  const typeDiff = compareLocale(a.media_type ?? "", b.media_type ?? "");
  if (typeDiff !== 0) return typeDiff;
  const statusDiff = compareLocale(a.status ?? "", b.status ?? "");
  if (statusDiff !== 0) return statusDiff;
  return compareAssetId(a.asset_id, b.asset_id);
}

function compareByType(a: AssetSummary, b: AssetSummary): number {
  const typeDiff = compareLocale(a.media_type ?? "", b.media_type ?? "");
  if (typeDiff !== 0) return typeDiff;
  const nameDiff = compareName(getAssetDisplayName(a), getAssetDisplayName(b));
  if (nameDiff !== 0) return nameDiff;
  const timeDiff = parseImportedAt(b.imported_at) - parseImportedAt(a.imported_at);
  if (timeDiff !== 0) return timeDiff;
  const statusDiff = compareLocale(a.status ?? "", b.status ?? "");
  if (statusDiff !== 0) return statusDiff;
  return compareAssetId(a.asset_id, b.asset_id);
}

function compareByStatus(a: AssetSummary, b: AssetSummary): number {
  const statusDiff = compareLocale(a.status ?? "", b.status ?? "");
  if (statusDiff !== 0) return statusDiff;
  const nameDiff = compareName(getAssetDisplayName(a), getAssetDisplayName(b));
  if (nameDiff !== 0) return nameDiff;
  const timeDiff = parseImportedAt(b.imported_at) - parseImportedAt(a.imported_at);
  if (timeDiff !== 0) return timeDiff;
  const typeDiff = compareLocale(a.media_type ?? "", b.media_type ?? "");
  if (typeDiff !== 0) return typeDiff;
  return compareAssetId(a.asset_id, b.asset_id);
}

function comparatorForSort(sort: LibrarySort): (a: AssetSummary, b: AssetSummary) => number {
  switch (sort) {
    case "oldest":
      return compareOldest;
    case "name":
      return compareByName;
    case "type":
      return compareByType;
    case "status":
      return compareByStatus;
    case "newest":
    default:
      return compareNewest;
  }
}

/**
 * Sort Assets without mutating the input. The returned array is a stable
 * input sequence for the waterfall: the comparator defines a total order
 * ending in `asset_id`.
 */
export function sortLibraryAssets(
  assets: readonly AssetSummary[],
  sort: LibrarySort,
): AssetSummary[] {
  const copy = [...assets];
  copy.sort(comparatorForSort(sort));
  return copy;
}

/**
 * Filter first, then sort. This is the single entry point the Library UI
 * uses to produce the waterfall input sequence.
 */
export function getOrderedLibraryAssets(
  assets: readonly AssetSummary[],
  options: LibraryOrderingOptions,
): AssetSummary[] {
  return sortLibraryAssets(
    filterLibraryAssets(assets, { media: options.media, status: options.status }),
    options.sort,
  );
}
