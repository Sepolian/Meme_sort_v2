/**
 * Library URL state and preference persistence (ticket 07).
 *
 * Single typed contract for deep-linkable Library state:
 * - URL owns `q`, `sort`, `media`, `status`, and `asset`.
 * - localStorage owns persisted `sort`, `media`, `status`, and `density`.
 * - Theme persistence belongs to ticket 18 and is never touched here.
 * - Semantic/image/similar payloads, image-search selection, open inspector
 *   as a preference, query text as a preference, and selection are transient
 *   and are never persisted.
 *
 * Precedence for sort/media/status: URL value, then persisted preference,
 * then default. Invalid URL or stored values fall back to defaults and invalid
 * URL values are normalized out of the address bar without a reload loop.
 */

export type LibrarySort = "newest" | "oldest" | "name" | "type" | "status";
export type LibraryMediaFilter = "all" | "still" | "gif";
export type LibraryStatusFilter = "all" | "indexed" | "pending" | "failed";
export type LibraryDensity = "comfortable" | "compact";

/**
 * Discriminated transient result mode. `browse` and `local` may be derived
 * from the URL (`q`); `semantic`, `image`, and `similar` are set only by
 * explicit user actions (tickets 11-12) and are never restored from URL or
 * localStorage.
 */
export type LibraryResultMode =
  | { kind: "browse" }
  | { kind: "local"; query: string }
  | { kind: "semantic"; query: string; requestId: string }
  | { kind: "image"; selectionId: string | null }
  | { kind: "similar"; assetId: string };

export const LIBRARY_SORTS: readonly LibrarySort[] = ["newest", "oldest", "name", "type", "status"];
export const LIBRARY_MEDIA_FILTERS: readonly LibraryMediaFilter[] = ["all", "still", "gif"];
export const LIBRARY_STATUS_FILTERS: readonly LibraryStatusFilter[] = ["all", "indexed", "pending", "failed"];
export const LIBRARY_DENSITIES: readonly LibraryDensity[] = ["comfortable", "compact"];

export const DEFAULT_LIBRARY_SORT: LibrarySort = "newest";
export const DEFAULT_LIBRARY_MEDIA: LibraryMediaFilter = "all";
export const DEFAULT_LIBRARY_STATUS: LibraryStatusFilter = "all";
export const DEFAULT_LIBRARY_DENSITY: LibraryDensity = "comfortable";

/** Versioned preference keys. Bumping the suffix invalidates obsolete values. */
export const LIBRARY_PREFERENCE_KEYS = {
  sort: "memesort.library.sort/v1",
  media: "memesort.library.media/v1",
  status: "memesort.library.status/v1",
  density: "memesort.library.density/v1",
} as const;

export interface LibraryUrlState {
  /** Raw query text from `q`. Empty string means absent (browse mode). */
  q: string;
  /** Valid URL enum value, or null when absent/invalid (persisted/default applies). */
  sort: LibrarySort | null;
  media: LibraryMediaFilter | null;
  status: LibraryStatusFilter | null;
  /** Inspector target from `asset`. Null when absent. */
  assetId: string | null;
}

export interface LibraryPreferences {
  sort: LibrarySort;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  density: LibraryDensity;
}

export interface EffectiveLibraryState {
  q: string;
  sort: LibrarySort;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  density: LibraryDensity;
  assetId: string | null;
}

export function isLibrarySort(value: unknown): value is LibrarySort {
  return typeof value === "string" && (LIBRARY_SORTS as readonly string[]).includes(value);
}

export function isLibraryMediaFilter(value: unknown): value is LibraryMediaFilter {
  return typeof value === "string" && (LIBRARY_MEDIA_FILTERS as readonly string[]).includes(value);
}

export function isLibraryStatusFilter(value: unknown): value is LibraryStatusFilter {
  return typeof value === "string" && (LIBRARY_STATUS_FILTERS as readonly string[]).includes(value);
}

export function isLibraryDensity(value: unknown): value is LibraryDensity {
  return typeof value === "string" && (LIBRARY_DENSITIES as readonly string[]).includes(value);
}

function toSearchParams(search: string | URLSearchParams): URLSearchParams {
  if (search instanceof URLSearchParams) return new URLSearchParams(search.toString());
  const trimmed = search.trim();
  if (!trimmed) return new URLSearchParams();
  // Accept both "?a=b" and "a=b".
  return new URLSearchParams(trimmed.startsWith("?") ? trimmed.slice(1) : trimmed);
}

function parseAssetId(raw: string | null): string | null {
  if (raw === null) return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed : null;
}

/**
 * Parse Library URL state. Invalid enum values become null so callers fall
 * back to persisted preferences/defaults and can normalize the URL.
 */
export function parseLibraryUrlState(search: string | URLSearchParams): LibraryUrlState {
  const params = toSearchParams(search);
  const sortRaw = params.get("sort");
  const mediaRaw = params.get("media");
  const statusRaw = params.get("status");
  return {
    q: params.get("q") ?? "",
    sort: sortRaw !== null && isLibrarySort(sortRaw) ? sortRaw : null,
    media: mediaRaw !== null && isLibraryMediaFilter(mediaRaw) ? mediaRaw : null,
    status: statusRaw !== null && isLibraryStatusFilter(statusRaw) ? statusRaw : null,
    assetId: parseAssetId(params.get("asset")),
  };
}

export interface SerializableLibraryUrlState {
  q?: string | null;
  sort?: LibrarySort | null;
  media?: LibraryMediaFilter | null;
  status?: LibraryStatusFilter | null;
  assetId?: string | null;
}

/**
 * Serialize Library state to URL params. Defaults, empty queries, and null
 * inspector targets are omitted to keep URLs minimal; absence means
 * "use persisted preference/default" for sort/media/status.
 */
export function serializeLibraryUrlState(state: SerializableLibraryUrlState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.q !== undefined && state.q !== null && state.q !== "") {
    params.set("q", state.q);
  }
  if (state.sort !== undefined && state.sort !== null && state.sort !== DEFAULT_LIBRARY_SORT) {
    params.set("sort", state.sort);
  }
  if (state.media !== undefined && state.media !== null && state.media !== DEFAULT_LIBRARY_MEDIA) {
    params.set("media", state.media);
  }
  if (state.status !== undefined && state.status !== null && state.status !== DEFAULT_LIBRARY_STATUS) {
    params.set("status", state.status);
  }
  if (state.assetId !== undefined && state.assetId !== null && state.assetId.trim() !== "") {
    params.set("asset", state.assetId.trim());
  }
  return params;
}

export function buildLibrarySearchString(state: SerializableLibraryUrlState): string {
  const serialized = serializeLibraryUrlState(state).toString();
  return serialized ? `?${serialized}` : "";
}

/**
 * Return a normalized search string with invalid enum values removed.
 * Valid params (including `q` and `asset`) are preserved byte-for-byte in
 * decoded meaning. Returns "" when nothing remains.
 */
export function normalizeLibrarySearchString(search: string | URLSearchParams): string {
  const parsed = parseLibraryUrlState(search);
  return buildLibrarySearchString({
    q: parsed.q || undefined,
    sort: parsed.sort,
    media: parsed.media,
    status: parsed.status,
    assetId: parsed.assetId,
  });
}

/**
 * True when the raw search string contains an invalid sort/media/status value
 * (i.e. a replace-navigation cleanup is needed). Empty `q`/`asset` values do
 * not trigger normalization by themselves to avoid noisy replaces; they are
 * dropped on the next explicit write.
 */
export function needsLibraryUrlNormalization(search: string | URLSearchParams): boolean {
  const rawParams = toSearchParams(search);
  if (rawParams.get("sort") !== null && !isLibrarySort(rawParams.get("sort"))) return true;
  if (rawParams.get("media") !== null && !isLibraryMediaFilter(rawParams.get("media"))) return true;
  if (rawParams.get("status") !== null && !isLibraryStatusFilter(rawParams.get("status"))) return true;
  return false;
}

function defaultStorage(): Storage | null {
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch {
    // jsdom/test environments without localStorage fall through.
  }
  return null;
}

function readStoredEnum<T>(storage: Storage | null, key: string, guard: (value: unknown) => value is T, fallback: T): T {
  if (!storage) return fallback;
  try {
    const raw = storage.getItem(key);
    if (raw !== null && guard(raw)) return raw;
  } catch {
    // Storage access can throw in private mode; fall back to defaults.
  }
  return fallback;
}

/** Load persisted sort/media/status/density, falling back safely on invalid values. */
export function loadLibraryPreferences(storage?: Storage | null): LibraryPreferences {
  const store = storage ?? defaultStorage();
  return {
    sort: readStoredEnum(store, LIBRARY_PREFERENCE_KEYS.sort, isLibrarySort, DEFAULT_LIBRARY_SORT),
    media: readStoredEnum(store, LIBRARY_PREFERENCE_KEYS.media, isLibraryMediaFilter, DEFAULT_LIBRARY_MEDIA),
    status: readStoredEnum(store, LIBRARY_PREFERENCE_KEYS.status, isLibraryStatusFilter, DEFAULT_LIBRARY_STATUS),
    density: readStoredEnum(store, LIBRARY_PREFERENCE_KEYS.density, isLibraryDensity, DEFAULT_LIBRARY_DENSITY),
  };
}

/** Persist a partial preference update without touching other keys or theme. */
export function saveLibraryPreferences(prefs: Partial<LibraryPreferences>, storage?: Storage | null): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    if (prefs.sort !== undefined) store.setItem(LIBRARY_PREFERENCE_KEYS.sort, prefs.sort);
    if (prefs.media !== undefined) store.setItem(LIBRARY_PREFERENCE_KEYS.media, prefs.media);
    if (prefs.status !== undefined) store.setItem(LIBRARY_PREFERENCE_KEYS.status, prefs.status);
    if (prefs.density !== undefined) store.setItem(LIBRARY_PREFERENCE_KEYS.density, prefs.density);
  } catch {
    // Quota/private-mode failures must not break Library browsing.
  }
}

/**
 * Resolve the effective Library state with URL > persisted > default
 * precedence for sort/media/status. `q` and `asset` are URL-only; `density`
 * is preference-only (no URL representation in the ticket 07 contract).
 */
export function resolveEffectiveLibraryState(url: LibraryUrlState, prefs: LibraryPreferences): EffectiveLibraryState {
  return {
    q: url.q,
    sort: url.sort ?? prefs.sort,
    media: url.media ?? prefs.media,
    status: url.status ?? prefs.status,
    density: prefs.density,
    assetId: url.assetId,
  };
}

/** Derive the initial transient result mode from URL state only. Never semantic. */
export function deriveInitialResultMode(url: LibraryUrlState): LibraryResultMode {
  if (url.q !== "") return { kind: "local", query: url.q };
  return { kind: "browse" };
}
