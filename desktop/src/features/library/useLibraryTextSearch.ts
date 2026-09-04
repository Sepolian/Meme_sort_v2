import { useCallback, useEffect, useRef, useState } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import type { SearchAsset } from "../../api/types";

interface UseLibraryTextSearchOptions {
  client: MemeSortClient;
}

interface UseLibraryTextSearchApi {
  /** Raw retrieval projections for the latest committed request (null until first success). */
  rawResults: SearchAsset[] | null;
  /** Query text of the latest committed results (null until first success). */
  committedQuery: string | null;
  isSearching: boolean;
  searchError: string | null;
  /** Latest request identity (in-flight id, or null when idle). Tracked independently of input text. */
  activeRequestId: string | null;
  /**
   * Start a UUID-scoped Search Request. Cancels the previous waiting request
   * first, returns the new request id (null when the query is empty).
   * Only the latest request identity may commit results or errors.
   */
  submitSearch: (query: string) => string | null;
  /** Cancel active work and discard committed results/errors. */
  clearSearch: () => void;
}

/**
 * UUID-scoped semantic text search with best-effort cancellation (ticket 11).
 *
 * - Typing never touches this hook; only explicit submit calls `submitSearch`.
 * - Before starting a new request, the previous waiting request is cancelled
 *   through the existing `cancelSearch(previousRequestId)` command.
 * - Clearing and unmounting cancel active work (best-effort).
 * - A monotonically tracked request identity ensures only the latest request
 *   may commit results or errors, even when an older promise settles later.
 */
export function useLibraryTextSearch({ client }: UseLibraryTextSearchOptions): UseLibraryTextSearchApi {
  const [rawResults, setRawResults] = useState<SearchAsset[] | null>(null);
  const [committedQuery, setCommittedQuery] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const [activeRequestIdState, setActiveRequestIdState] = useState<string | null>(null);

  const cancelRequest = useCallback(
    (requestId: string | null) => {
      if (!requestId) return;
      void client.cancelSearch(requestId);
    },
    [client],
  );

  const clearSearch = useCallback(() => {
    const previous = activeRequestId.current;
    activeRequestId.current = null;
    setActiveRequestIdState(null);
    cancelRequest(previous);
    setRawResults(null);
    setCommittedQuery(null);
    setSearchError(null);
    setIsSearching(false);
  }, [cancelRequest]);

  // Leaving Library cancels active work. Best-effort: clear identity first so
  // a late settlement cannot commit after unmount.
  useEffect(() => {
    return () => {
      const previous = activeRequestId.current;
      if (previous) {
        activeRequestId.current = null;
        void client.cancelSearch(previous);
      }
    };
  }, [client]);

  const submitSearch = useCallback(
    (query: string): string | null => {
      const trimmed = query.trim();
      if (!trimmed) return null;
      const previous = activeRequestId.current;
      if (previous) {
        activeRequestId.current = null;
        void client.cancelSearch(previous);
      }
      const requestId = crypto.randomUUID();
      activeRequestId.current = requestId;
      setActiveRequestIdState(requestId);
      setIsSearching(true);
      setSearchError(null);

      void (async () => {
        try {
          const result = await client.searchText(trimmed, requestId);
          if (activeRequestId.current === requestId) {
            setRawResults(result.results);
            setCommittedQuery(trimmed);
          }
        } catch (error) {
          if (activeRequestId.current === requestId) {
            setSearchError(
              tauriErrorDetail(error, "MemeSort could not complete this Search Request."),
            );
          }
        } finally {
          if (activeRequestId.current === requestId) {
            activeRequestId.current = null;
            setActiveRequestIdState(null);
            setIsSearching(false);
          }
        }
      })();

      return requestId;
    },
    [client],
  );

  return {
    rawResults,
    committedQuery,
    isSearching,
    searchError,
    activeRequestId: activeRequestIdState,
    submitSearch,
    clearSearch,
  };
}

export default useLibraryTextSearch;
