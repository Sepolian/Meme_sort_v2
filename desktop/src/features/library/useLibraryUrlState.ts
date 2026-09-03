import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  DEFAULT_LIBRARY_DENSITY,
  DEFAULT_LIBRARY_MEDIA,
  DEFAULT_LIBRARY_SORT,
  DEFAULT_LIBRARY_STATUS,
  deriveInitialResultMode,
  loadLibraryPreferences,
  needsLibraryUrlNormalization,
  normalizeLibrarySearchString,
  parseLibraryUrlState,
  resolveEffectiveLibraryState,
  saveLibraryPreferences,
  type LibraryDensity,
  type LibraryMediaFilter,
  type LibraryPreferences,
  type LibraryResultMode,
  type LibrarySort,
  type LibraryStatusFilter,
} from "./libraryUrlState";

export interface LibraryUrlStateApi {
  q: string;
  sort: LibrarySort;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  density: LibraryDensity;
  assetId: string | null;
  resultMode: LibraryResultMode;
  preferences: LibraryPreferences;
  setQuery: (query: string) => void;
  clearQuery: () => void;
  setSort: (sort: LibrarySort) => void;
  setMedia: (media: LibraryMediaFilter) => void;
  setStatus: (status: LibraryStatusFilter) => void;
  setDensity: (density: LibraryDensity) => void;
  setAssetId: (assetId: string) => void;
  clearAssetId: () => void;
  setResultMode: (mode: LibraryResultMode) => void;
  resetResultMode: () => void;
}

function resolveStorage(explicit: Storage | null | undefined): Storage | null {
  if (explicit !== undefined) return explicit;
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch {
    return null;
  }
  return null;
}

/**
 * URL-backed Library state with versioned preference persistence (ticket 07).
 *
 * - Reads `q/sort/media/status/asset` from the URL on every render.
 * - Resolves sort/media/status as URL > persisted > default.
 * - Persists sort/media/status/density to versioned localStorage keys.
 * - Keeps `resultMode` transient: URL `q` yields `local`, never `semantic`.
 * - Normalizes invalid enum values with a single replace navigation.
 */
export function useLibraryUrlState(explicitStorage?: Storage | null): LibraryUrlStateApi {
  const [searchParams, setSearchParams] = useSearchParams();
  const storage = useMemo(() => resolveStorage(explicitStorage), [explicitStorage]);

  const [preferences, setPreferences] = useState<LibraryPreferences>(() => loadLibraryPreferences(storage));

  // Re-read persisted preferences if a different storage instance is injected
  // (tests) or when the window regains focus after an external write.
  useEffect(() => {
    setPreferences(loadLibraryPreferences(storage));
  }, [storage]);

  const urlState = useMemo(() => parseLibraryUrlState(searchParams), [searchParams]);
  const effective = useMemo(() => resolveEffectiveLibraryState(urlState, preferences), [urlState, preferences]);

  const [resultMode, setResultModeState] = useState<LibraryResultMode>(() => deriveInitialResultMode(urlState));

  // Keep transient local/browse mode aligned with URL q while never
  // auto-entering semantic/image/similar from URL or storage. A mismatched
  // semantic query is stale once the user types a new q, so fall back to
  // local; image/similar are query-independent and stay sticky until tickets
  // 11-12 explicitly reset them.
  useEffect(() => {
    setResultModeState((current) => {
      if (current.kind === "semantic") {
        if (urlState.q === "") return { kind: "browse" };
        if (current.query !== urlState.q) return { kind: "local", query: urlState.q };
        return current;
      }
      if (current.kind === "image" || current.kind === "similar") return current;
      if (urlState.q !== "" && (current.kind !== "local" || current.query !== urlState.q)) {
        return { kind: "local", query: urlState.q };
      }
      if (urlState.q === "" && current.kind !== "browse") return { kind: "browse" };
      return current;
    });
  }, [urlState.q]);

  // Normalize invalid enum values once without a reload loop.
  useEffect(() => {
    if (needsLibraryUrlNormalization(searchParams)) {
      const normalized = normalizeLibrarySearchString(searchParams);
      const next = new URLSearchParams(normalized.replace(/^\?/, ""));
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const updateParams = useCallback(
    (mutate: (next: URLSearchParams) => void, replace = false) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      setSearchParams(next, replace ? { replace: true } : undefined);
    },
    [searchParams, setSearchParams],
  );

  const setQuery = useCallback(
    (query: string) => {
      updateParams((next) => {
        if (query === "") next.delete("q");
        else next.set("q", query);
      });
    },
    [updateParams],
  );

  const clearQuery = useCallback(() => {
    updateParams((next) => {
      next.delete("q");
    });
  }, [updateParams]);

  const persistAndReflect = useCallback(
    (key: "sort" | "media" | "status", param: string, value: string, isDefault: boolean) => {
      const patch: Partial<LibraryPreferences> =
        key === "sort"
          ? { sort: value as LibrarySort }
          : key === "media"
            ? { media: value as LibraryMediaFilter }
            : { status: value as LibraryStatusFilter };
      setPreferences((current) => ({ ...current, ...patch }));
      saveLibraryPreferences(patch, storage);
      updateParams((next) => {
        if (isDefault) next.delete(param);
        else next.set(param, value);
      });
    },
    [storage, updateParams],
  );

  const setSort = useCallback(
    (sort: LibrarySort) => persistAndReflect("sort", "sort", sort, sort === DEFAULT_LIBRARY_SORT),
    [persistAndReflect],
  );

  const setMedia = useCallback(
    (media: LibraryMediaFilter) => persistAndReflect("media", "media", media, media === DEFAULT_LIBRARY_MEDIA),
    [persistAndReflect],
  );

  const setStatus = useCallback(
    (status: LibraryStatusFilter) => persistAndReflect("status", "status", status, status === DEFAULT_LIBRARY_STATUS),
    [persistAndReflect],
  );

  const setDensity = useCallback(
    (density: LibraryDensity) => {
      setPreferences((current) => ({ ...current, density }));
      saveLibraryPreferences({ density }, storage);
    },
    [storage],
  );

  const setAssetId = useCallback(
    (assetId: string) => {
      const trimmed = assetId.trim();
      if (!trimmed) return;
      updateParams((next) => {
        next.set("asset", trimmed);
      });
    },
    [updateParams],
  );

  const clearAssetId = useCallback(() => {
    updateParams((next) => {
      next.delete("asset");
    });
  }, [updateParams]);

  const setResultMode = useCallback((mode: LibraryResultMode) => {
    setResultModeState(mode);
  }, []);

  const resetResultMode = useCallback(() => {
    setResultModeState((current) => {
      if (current.kind === "local" || current.kind === "browse") return current;
      return { kind: "browse" };
    });
  }, []);

  return {
    q: effective.q,
    sort: effective.sort,
    media: effective.media,
    status: effective.status,
    density: preferences.density ?? DEFAULT_LIBRARY_DENSITY,
    assetId: effective.assetId,
    resultMode,
    preferences,
    setQuery,
    clearQuery,
    setSort,
    setMedia,
    setStatus,
    setDensity,
    setAssetId,
    clearAssetId,
    setResultMode,
    resetResultMode,
  };
}

export default useLibraryUrlState;
