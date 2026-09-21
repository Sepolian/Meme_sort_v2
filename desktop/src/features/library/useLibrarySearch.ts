import { useCallback, useEffect, useRef, useState } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorCode, tauriErrorDetail } from "../../api/tauri-error";
import type { SearchAsset } from "../../api/types";
import type { LibraryResultMode } from "./libraryUrlState";

type SearchTarget =
  | { kind: "text"; query: string }
  | { kind: "image"; selectionId: string; label: string }
  | { kind: "similar"; assetId: string };

type RetrievalRequest = SearchTarget & { id: string };

type CurrentSearch =
  | { status: "idle"; request: null; error: null }
  | { status: "loading" | "success"; request: RetrievalRequest; error: null }
  | { status: "error"; request: RetrievalRequest; error: string; errorCode: string | null; retryable: boolean };

interface CommittedResult {
  request: RetrievalRequest;
  assets: SearchAsset[];
}

export interface LibrarySearchView {
  current: CurrentSearch;
  /** May be a retained success, with its original identity, during a retry. */
  result: CommittedResult | null;
}

export const EMPTY_LIBRARY_SEARCH: LibrarySearchView = {
  current: { status: "idle", request: null, error: null },
  result: null,
};

interface SearchState {
  current: CurrentSearch;
  /** Last success per mode preserves the existing retry/display policy. */
  committed: Partial<Record<SearchTarget["kind"], CommittedResult>>;
}

const EMPTY_STATE: SearchState = { current: EMPTY_LIBRARY_SEARCH.current, committed: {} };

function visibleResult(mode: LibraryResultMode, committed: SearchState["committed"]): CommittedResult | null {
  // Text and similar retries keep the last success for the same input, even
  // across mode changes. Input equality is a display policy, never permission
  // for a pending request to commit. New image selections always start fresh.
  switch (mode.kind) {
    case "semantic": {
      const result = committed.text;
      return result?.request.kind === "text" && result.request.query === mode.query ? result : null;
    }
    case "image": {
      const result = committed.image;
      return result?.request.kind === "image" && result.request.selectionId === mode.selectionId ? result : null;
    }
    case "similar": {
      const result = committed.similar;
      return result?.request.kind === "similar" && result.request.assetId === mode.assetId ? result : null;
    }
    default:
      return null;
  }
}

function requestMatchesMode(request: RetrievalRequest, mode: LibraryResultMode): boolean {
  switch (mode.kind) {
    case "semantic":
      // Retries get a fresh request ID while remaining the same semantic
      // result mode; the query is the durable frontend identity here.
      return request.kind === "text" && request.query === mode.query;
    case "image":
      return request.kind === "image" && request.selectionId === mode.selectionId;
    case "similar":
      return request.kind === "similar" && request.assetId === mode.assetId;
    default:
      return false;
  }
}

/** Coordinates all Library retrieval; only the latest frontend identity may commit. */
export function useLibrarySearch({ client, resultMode }: {
  client: MemeSortClient;
  resultMode: LibraryResultMode;
}) {
  const [state, setState] = useState<SearchState>(EMPTY_STATE);
  const activeRequest = useRef<RetrievalRequest | null>(null);
  const mounted = useRef(true);

  const cancelPrevious = useCallback(() => {
    const previous = activeRequest.current;
    // Invalidate before attempting transport cancellation, including when it fails.
    activeRequest.current = null;
    if (previous && previous.kind !== "similar") {
      void (async () => {
        try {
          await client.cancelSearch(previous.id);
        } catch {
          // Best-effort: neither rejection nor a synchronous client error blocks browsing.
        }
      })();
    }
  }, [client]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cancelPrevious();
    };
  }, [cancelPrevious]);

  // URL q changes can arrive from browser back/forward without going through
  // the search bar. Once the URL-derived mode no longer owns the request,
  // invalidate and cancel that obsolete visual/text work as well.
  useEffect(() => {
    const active = activeRequest.current;
    if (active && !requestMatchesMode(active, resultMode)) cancelPrevious();
  }, [cancelPrevious, resultMode]);

  const clearSearch = useCallback(() => {
    cancelPrevious();
    setState(EMPTY_STATE);
  }, [cancelPrevious]);

  const execute = useCallback((target: SearchTarget, requestId: string = crypto.randomUUID()): string | null => {
    // A native picker can resolve after Library has unmounted.
    if (!mounted.current) return null;
    cancelPrevious();
    const request: RetrievalRequest = { ...target, id: requestId };
    activeRequest.current = request;
    setState((previous) => ({
      ...previous,
      current: { status: "loading", request, error: null },
    }));

    void (async () => {
      try {
        const response = await (() => {
          switch (request.kind) {
            case "text": return client.searchText(request.query, request.id);
            case "image": return client.searchImage(request.id);
            // Find Similar has a frontend identity, but no cancellable backend Search Request.
            case "similar": return client.findSimilar(request.assetId);
          }
        })();
        if (activeRequest.current?.id === request.id) {
          setState((previous) => ({
            current: { status: "success", request, error: null },
            committed: { ...previous.committed, [request.kind]: { request, assets: response.results } },
          }));
        }
      } catch (error) {
        if (activeRequest.current?.id === request.id) {
          const message = tauriErrorDetail(error, request.kind === "similar"
            ? "MemeSort could not find similar Assets."
            : "MemeSort could not complete this Search Request.");
          const errorCode = tauriErrorCode(error);
          const retryable = errorCode !== "ImageSelectionUnavailable" && !(
            typeof error === "object"
            && error !== null
            && (error as { retryable?: unknown }).retryable === false
          );
          setState((previous) => ({
            ...previous,
            current: {
              status: "error",
              request,
              error: message,
              errorCode,
              retryable,
            },
          }));
        }
      } finally {
        if (activeRequest.current?.id === request.id) activeRequest.current = null;
      }
    })();
    return request.id;
  }, [client, cancelPrevious]);

  const submitSearch = useCallback((query: string) => {
    const trimmed = query.trim();
    return trimmed ? execute({ kind: "text", query: trimmed }) : null;
  }, [execute]);
  const submitImageSearch = useCallback((selectionId: string, label: string, requestId?: string) => {
    const trimmedId = selectionId.trim();
    if (!trimmedId) return null;
    return execute({ kind: "image", selectionId: trimmedId, label: label.trim() || "selected image" }, requestId);
  }, [execute]);
  const submitSimilarSearch = useCallback((assetId: string) => {
    const trimmed = assetId.trim();
    return trimmed ? execute({ kind: "similar", assetId: trimmed }) : null;
  }, [execute]);
  const retrySearch = useCallback(() => {
    const current = state.current;
    if (current.status !== "error" || !current.retryable) return null;
    switch (current.request.kind) {
      case "text": return execute({ kind: "text", query: current.request.query });
      case "image": return execute({
        kind: "image",
        selectionId: current.request.selectionId,
        label: current.request.label,
      }, current.request.id);
      case "similar": return execute({ kind: "similar", assetId: current.request.assetId });
    }
  }, [execute, state]);

  const search: LibrarySearchView = {
    current: state.current,
    result: visibleResult(resultMode, state.committed),
  };
  return { search, submitSearch, submitImageSearch, submitSimilarSearch, retrySearch, clearSearch };
}
