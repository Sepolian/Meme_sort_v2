import { useCallback, useEffect, useRef, useState } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import type { SearchAsset } from "../../api/types";

interface UseLibraryTextSearchOptions {
  client: MemeSortClient;
}

interface UseLibraryTextSearchApi {
  /** Raw text retrieval projections for the latest committed text request (null until first success). */
  rawResults: SearchAsset[] | null;
  /** Query text of the latest committed text results (null until first success). */
  committedQuery: string | null;
  /** Raw image retrieval projections for the latest committed image request (null until first success). */
  imageRawResults: SearchAsset[] | null;
  /** Request ID of the latest committed image results (null until first success). */
  committedImageRequestId: string | null;
  /** Raw similar retrieval projections for the latest committed similar request (null until first success). */
  similarRawResults: SearchAsset[] | null;
  /** Asset ID of the latest committed similar results (null until first success). */
  committedSimilarAssetId: string | null;
  isSearching: boolean;
  searchError: string | null;
  /** Latest request identity (in-flight id, or null when idle). Tracked independently of input text. */
  activeRequestId: string | null;
  /** Kind of the in-flight request, or null when idle. Shared across modes for latest-wins. */
  activeKind: "text" | "image" | "similar" | null;
  /**
   * Start a UUID-scoped Search Request. Cancels the previous waiting request
   * first, returns the new request id (null when the query is empty).
   * Only the latest request identity may commit results or errors.
   */
  submitSearch: (query: string) => string | null;
  /**
   * Start a UUID-scoped image Search Request via `searchImage(requestId)`.
   * Cancels the previous cancellable request first. Only the latest request
   * identity may commit. The caller must have completed `chooseSearchImage`
   * (null selection means cancel and must not call this).
   */
  submitImageSearch: () => string;
  /**
   * Start a Find Similar request via `findSimilar(assetId)`. Cancels the
   * previous cancellable text/image request first (similar itself has no
   * backend request ID to cancel). Only the latest identity may commit.
   * Returns the internal request identity (null when the asset ID is empty).
   */
  submitSimilarSearch: (assetId: string) => string | null;
  /** Cancel active work and discard committed results/errors. */
  clearSearch: () => void;
}

/**
 * UUID-scoped semantic text search with best-effort cancellation (ticket 11),
 * extended to image and similar flows with shared latest-wins (ticket 12).
 *
 * - Typing never touches this hook; only explicit submit calls `submitSearch`,
 *   `submitImageSearch`, or `submitSimilarSearch`.
 * - Before starting a new request, the previous cancellable waiting request
 *   (text/image, which carry a backend `requestId`) is cancelled through the
 *   existing `cancelSearch(previousRequestId)` command. Similar requests carry
 *   only an internal identity and have no backend ID to cancel, but they still
 *   bump the shared identity so older completions cannot win.
 * - Clearing and unmounting cancel active cancellable work (best-effort).
 * - A monotonically tracked shared request identity ensures only the latest
 *   request may commit results or errors, even when an older promise settles
 *   later or when modes interleave (cross-mode latest-wins).
 */
export function useLibraryTextSearch({ client }: UseLibraryTextSearchOptions): UseLibraryTextSearchApi {
  const [rawResults, setRawResults] = useState<SearchAsset[] | null>(null);
  const [committedQuery, setCommittedQuery] = useState<string | null>(null);
  const [imageRawResults, setImageRawResults] = useState<SearchAsset[] | null>(null);
  const [committedImageRequestId, setCommittedImageRequestId] = useState<string | null>(null);
  const [similarRawResults, setSimilarRawResults] = useState<SearchAsset[] | null>(null);
  const [committedSimilarAssetId, setCommittedSimilarAssetId] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const activeKind = useRef<"text" | "image" | "similar" | null>(null);
  const [activeRequestIdState, setActiveRequestIdState] = useState<string | null>(null);
  const [activeKindState, setActiveKindState] = useState<"text" | "image" | "similar" | null>(null);

  const cancelPrevious = useCallback(() => {
    const previous = activeRequestId.current;
    const previousKind = activeKind.current;
    // Only text/image carry a backend requestId that `cancelSearch` knows.
    // Similar carries an internal identity only; bumping it is enough.
    if (previous && (previousKind === "text" || previousKind === "image")) {
      void client.cancelSearch(previous);
    }
    activeRequestId.current = null;
    activeKind.current = null;
  }, [client]);

  const clearSearch = useCallback(() => {
    cancelPrevious();
    setActiveRequestIdState(null);
    setActiveKindState(null);
    setRawResults(null);
    setCommittedQuery(null);
    setImageRawResults(null);
    setCommittedImageRequestId(null);
    setSimilarRawResults(null);
    setCommittedSimilarAssetId(null);
    setSearchError(null);
    setIsSearching(false);
  }, [cancelPrevious]);

  // Leaving Library cancels active cancellable work. Best-effort: clear
  // identity first so a late settlement cannot commit after unmount.
  useEffect(() => {
    return () => {
      const previous = activeRequestId.current;
      const previousKind = activeKind.current;
      if (previous && (previousKind === "text" || previousKind === "image")) {
        activeRequestId.current = null;
        activeKind.current = null;
        void client.cancelSearch(previous);
      } else {
        activeRequestId.current = null;
        activeKind.current = null;
      }
    };
  }, [client]);

  const submitSearch = useCallback(
    (query: string): string | null => {
      const trimmed = query.trim();
      if (!trimmed) return null;
      cancelPrevious();
      const requestId = crypto.randomUUID();
      activeRequestId.current = requestId;
      activeKind.current = "text";
      setActiveRequestIdState(requestId);
      setActiveKindState("text");
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
            activeKind.current = null;
            setActiveRequestIdState(null);
            setActiveKindState(null);
            setIsSearching(false);
          }
        }
      })();

      return requestId;
    },
    [client, cancelPrevious],
  );

  const submitImageSearch = useCallback((): string => {
    cancelPrevious();
    const requestId = crypto.randomUUID();
    activeRequestId.current = requestId;
    activeKind.current = "image";
    setActiveRequestIdState(requestId);
    setActiveKindState("image");
    setIsSearching(true);
    setSearchError(null);

    void (async () => {
      try {
        const result = await client.searchImage(requestId);
        if (activeRequestId.current === requestId) {
          setImageRawResults(result.results);
          setCommittedImageRequestId(requestId);
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
          activeKind.current = null;
          setActiveRequestIdState(null);
          setActiveKindState(null);
          setIsSearching(false);
        }
      }
    })();

    return requestId;
  }, [client, cancelPrevious]);

  const submitSimilarSearch = useCallback(
    (assetId: string): string | null => {
      const trimmed = assetId.trim();
      if (!trimmed) return null;
      cancelPrevious();
      // Similar has no backend requestId; track an internal identity so
      // cross-mode latest-wins still applies (older text/image completions
      // cannot overwrite a newer similar request and vice versa).
      const internalId = crypto.randomUUID();
      activeRequestId.current = internalId;
      activeKind.current = "similar";
      setActiveRequestIdState(internalId);
      setActiveKindState("similar");
      setIsSearching(true);
      setSearchError(null);

      void (async () => {
        try {
          const result = await client.findSimilar(trimmed);
          if (activeRequestId.current === internalId) {
            setSimilarRawResults(result.results);
            setCommittedSimilarAssetId(trimmed);
          }
        } catch (error) {
          if (activeRequestId.current === internalId) {
            setSearchError(
              tauriErrorDetail(error, "MemeSort could not find similar Assets."),
            );
          }
        } finally {
          if (activeRequestId.current === internalId) {
            activeRequestId.current = null;
            activeKind.current = null;
            setActiveRequestIdState(null);
            setActiveKindState(null);
            setIsSearching(false);
          }
        }
      })();

      return internalId;
    },
    [client, cancelPrevious],
  );

  return {
    rawResults,
    committedQuery,
    imageRawResults,
    committedImageRequestId,
    similarRawResults,
    committedSimilarAssetId,
    isSearching,
    searchError,
    activeRequestId: activeRequestIdState,
    activeKind: activeKindState,
    submitSearch,
    submitImageSearch,
    submitSimilarSearch,
    clearSearch,
  };
}

export default useLibraryTextSearch;
