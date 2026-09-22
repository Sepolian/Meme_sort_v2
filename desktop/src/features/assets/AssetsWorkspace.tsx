import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import {
  subscribeNativeDrag,
  type NativeDragSubscribe,
} from "../../api/native-drag";
import { useImportBatch } from "../import/ImportBatchContext";
import { importBatchBlockedMessage } from "../import/import-status";
import { useOptionalRuntimeHealth } from "../runtime/useRuntimeHealth";
import { AssetWaterfall } from "./AssetWaterfall";
import { buildAssetSummaryMap, composeSearchItems, type ComposedSearchItem } from "../../api/result-models";
import { filterLocalAssets } from "../library/librarySearch";
import {
  DEFAULT_LIBRARY_DENSITY,
  DEFAULT_LIBRARY_MEDIA,
  DEFAULT_LIBRARY_SORT,
  DEFAULT_LIBRARY_STATUS,
  type LibraryDensity,
  type LibraryMediaFilter,
  type LibraryResultMode,
  type LibrarySort,
  type LibraryStatusFilter,
} from "../library/libraryUrlState";
import {
  getAssetDisplayName,
  getOrderedLibraryAssets,
} from "../library/libraryOrdering";
import { EMPTY_LIBRARY_SEARCH, type LibrarySearchView } from "../library/useLibrarySearch";
import { useDeletedAssetReconciliation } from "./useDeletedAssetReconciliation";
import type { AssetSummary } from "../../api/types";
import {
  isRestorableFocusTarget,
  scheduleFocusRestoration,
} from "../../components/useEscapeSurface";

interface AssetsWorkspaceProps {
  client: MemeSortClient;
  selectedAssetId: string | null;
  onSelectAsset: (assetId: string) => void;
  onCloseDetail: () => void;
  nativeDrag?: NativeDragSubscribe;
  sort?: LibrarySort;
  media?: LibraryMediaFilter;
  status?: LibraryStatusFilter;
  density?: LibraryDensity;
  onClearFilters?: () => void;
  /** URL query text driving local filtering (typing updates `q`). */
  query?: string;
  /** Transient result mode from ticket 07 (`browse`/`local`/`semantic`/`image`/`similar`). */
  resultMode?: LibraryResultMode;
  /** Coordinated request state and results already selected for this mode. */
  search?: LibrarySearchView;
  /** Clear all search modes and restore browsing (cancels active work). */
  onClearSearch?: () => void;
  /** Retry the current retrieval while preserving its result context. */
  onRetrySearch?: () => void;
  /** Re-open the native image picker when the previous image payload is gone. */
  onChooseImage?: () => void;
  /** Safe filename for the currently selected visual query. */
  imageQueryLabel?: string | null;
  /** Whether the native side still has the selected image payload. */
  imageSelectionAvailable?: boolean;
  /**
   * Find Similar entry point for ticket 12 (card hover/context action).
   * Shares the same result mode as the inspector entry point.
   */
  onFindSimilar?: (assetId: string) => void;
}

type BatchMutationRequest = { action: "delete" | "rebuild-active-index"; assetIds: string[] };

type LibraryNotice = { kind: "error" | "success"; text: string };

interface CopyOperation {
  generation: number;
  assetIds: string[];
}

interface ConfirmAction {
  title: string;
  detail: string;
  confirmLabel: string;
  request: BatchMutationRequest;
}

function mutationSummary(request: BatchMutationRequest, result: Awaited<ReturnType<MemeSortClient["batchAssetAction"]>>): string {
  return request.action === "delete"
    ? `Deleted ${result.affected_asset_ids.length} Asset(s).`
    : `Queued ${result.reindex_jobs_created} Active Index rebuild(s); skipped ${result.skipped_running_asset_ids.length} running Asset(s).`;
}

function AdvancedDetails({ items }: { items: readonly ComposedSearchItem[] }) {
  return (
    <details>
      <summary>Advanced details</summary>
      <ul className="detail-list">
        {items.map((item) => (
          <li key={item.summary.asset_id}>
            <span className="mono">{item.summary.asset_id}</span>
            <span>
              score {item.score.toFixed(3)} &middot; {item.matchSources.join(" + ") || "no match source"}
              {item.ocrSnippet ? ` \u00B7 ${item.ocrSnippet}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function assetCount(count: number): string {
  return `${count} Asset${count === 1 ? "" : "s"}`;
}

function browseFilterSummary(media: LibraryMediaFilter, status: LibraryStatusFilter): string | null {
  const filters = [
    media === "still" ? "Stills" : media === "gif" ? "GIFs" : null,
    status === "indexed" ? "Indexed" : status === "pending" ? "Pending" : status === "failed" ? "Failed" : null,
  ].filter((value): value is string => value !== null);
  return filters.length ? `Filters: ${filters.join(" · ")}` : null;
}

function BrowseResultContext({
  total,
  visible,
  media,
  status,
}: {
  total: number;
  visible: number;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
}) {
  const filterSummary = browseFilterSummary(media, status);
  return (
    <p className="library-result-context" role="status" aria-label="Browse results">
      <strong>{filterSummary ? `Showing ${visible} of ${assetCount(total)}` : assetCount(total)}</strong>
      <span>{filterSummary ?? "Browse mode · all loaded Assets."}</span>
    </p>
  );
}

function ActiveFilterRecovery({
  media,
  status,
  onClearFilters,
}: {
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  onClearFilters?: () => void;
}) {
  const summary = browseFilterSummary(media, status);
  if (!summary || !onClearFilters) return null;
  return (
    <>
      <span>{summary.replace("Filters:", "Active filters:")} may hide matches from this search.</span>
      <button className="button button-secondary" type="button" onClick={() => onClearFilters()}>
        Clear filters
      </button>
    </>
  );
}

function FilteredRetrievalEmpty({
  label,
  count,
  media,
  status,
  onClearFilters,
  onClearSearch,
  wallRef,
}: {
  label: string;
  count: number;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  onClearFilters?: () => void;
  onClearSearch?: () => void;
  wallRef: RefObject<HTMLDivElement | null>;
}) {
  const summary = browseFilterSummary(media, status);
  return (
    <div className="empty-state" aria-label={`${label} results filtered out`} ref={wallRef}>
      <h2>{count} {label.toLowerCase()} match{count === 1 ? " is" : "es are"} hidden by filters</h2>
      <p>{summary?.replace("Filters:", "Active filters:")} excludes every match from this search.</p>
      <div className="import-actions">
        <button className="button button-secondary" type="button" onClick={() => onClearFilters?.()}>
          Clear filters
        </button>
        <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
          Clear search
        </button>
      </div>
    </div>
  );
}

export function AssetsWorkspace({
  client,
  selectedAssetId,
  onSelectAsset,
  onCloseDetail,
  nativeDrag,
  sort = DEFAULT_LIBRARY_SORT,
  media = DEFAULT_LIBRARY_MEDIA,
  status = DEFAULT_LIBRARY_STATUS,
  density = DEFAULT_LIBRARY_DENSITY,
  onClearFilters,
  query = "",
  resultMode,
  search = EMPTY_LIBRARY_SEARCH,
  onClearSearch,
  onRetrySearch,
  onChooseImage,
  imageQueryLabel,
  imageSelectionAvailable = false,
  onFindSimilar,
}: AssetsWorkspaceProps) {
  const queryClient = useQueryClient();
  const importBatch = useImportBatch();
  const runtimeHealth = useOptionalRuntimeHealth();
  const indexingBlocked = runtimeHealth ? !runtimeHealth.isAuthorized : false;
  const startBatch = importBatch.startBatch;
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [confirmation, setConfirmation] = useState<ConfirmAction | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [libraryNotice, setLibraryNotice] = useState<LibraryNotice | null>(null);
  // Ticket 17: batch Copy original files feedback. Selection is preserved on
  // both success and failure; failure never claims clipboard restoration.
  const [copyPending, setCopyPending] = useState(false);
  const [copyNotice, setCopyNotice] = useState<LibraryNotice | null>(null);
  const copyGenerationRef = useRef(0);
  const copyOperationRef = useRef<CopyOperation | null>(null);
  const invalidateCopyOperation = useCallback(() => {
    copyGenerationRef.current += 1;
    copyOperationRef.current = null;
    setCopyPending(false);
    setCopyNotice(null);
  }, []);
  const [dragPreview, setDragPreview] = useState<{ itemCount: number } | null>(null);
  const wallRef = useRef<HTMLDivElement | null>(null);
  const startedDropIdRef = useRef<string | null>(null);
  const nativeDragSubscribe = nativeDrag ?? subscribeNativeDrag;
  const assetsQuery = useQuery({ queryKey: ["assets"], queryFn: () => client.getAssets() });
  const reconcileDeletedAssets = useDeletedAssetReconciliation({
    inspectedAssetId: selectedAssetId,
    onCloseDetail,
  });
  const mutation = useMutation({
    mutationFn: async (request: BatchMutationRequest) => client.batchAssetAction(request.action, request.assetIds),
    onSuccess: async (result, request) => {
      setFeedback(mutationSummary(request, result));
      setConfirmation(null);
      if (request.action === "delete") {
        invalidateCopyOperation();
        // Ticket 17: only Delete reconciles selection (removing exactly
        // `affected_asset_ids` so skipped/failed IDs are retained). Rebuild
        // preserves the full selection and never closes the inspector because
        // the Assets still exist.
        const affected = new Set(result.affected_asset_ids);
        setSelectedIds((current) => {
          const next = new Set([...current].filter((id) => !affected.has(id)));
          return next;
        });
        await reconcileDeletedAssets(result.affected_asset_ids);
      } else {
        await Promise.all([queryClient.invalidateQueries({ queryKey: ["assets"] }), queryClient.invalidateQueries({ queryKey: ["app-state"] })]);
      }
    },
    onError: () => { setFeedback("The requested Asset change could not be completed. The Library was not modified by the desktop UI."); setConfirmation(null); },
  });

  useEffect(() => {
    const pointerIsOverWall = (x: number, y: number) => {
      const rect = wallRef.current?.getBoundingClientRect();
      return Boolean(rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom);
    };
    const startFromDrop = async (dropId: string, itemCount: number) => {
      const label = `${itemCount} dropped item${itemCount === 1 ? "" : "s"}`;
      setLibraryNotice({ kind: "success", text: `Starting Import Batch for ${label}.` });
      try {
        const outcome = await startBatch(() => client.startLibraryImport(dropId));
        if (outcome.kind === "blocked") {
          setLibraryNotice({ kind: "error", text: importBatchBlockedMessage(outcome.reason) });
          return;
        }
        setLibraryNotice({ kind: "success", text: `Import Batch started for ${label}.` });
      } catch (error) {
        setLibraryNotice({ kind: "error", text: tauriErrorDetail(error, "MemeSort could not start the Import Batch from this drop. Resolve the conflict or make a fresh native selection to retry.") });
      }
    };
    return nativeDragSubscribe((summary) => {
      if (summary.phase === "leave") {
        setDragPreview(null);
        return;
      }
      if (summary.phase === "drop") {
        setDragPreview(null);
        if (!summary.accepted || !summary.dropId || !pointerIsOverWall(summary.x, summary.y)) return;
        if (startedDropIdRef.current === summary.dropId) return;
        startedDropIdRef.current = summary.dropId;
        void startFromDrop(summary.dropId, summary.fileCount + summary.folderCount);
        return;
      }
      setDragPreview(
        summary.accepted && pointerIsOverWall(summary.x, summary.y)
          ? { itemCount: summary.fileCount + summary.folderCount }
          : null,
      );
    });
  }, [client, nativeDragSubscribe, queryClient, startBatch]);

  // Ticket 08: filter first, then sort, to produce the stable waterfall input
  // sequence. This hook stays above the pending/error early returns so hook
  // order never changes between loading and loaded renders. Density only
  // affects presentation (column width/gap) via the grid's data-density
  // attribute and ticket 07's persisted preference.
  const assetsData = assetsQuery.data;
  // Only the committed layout effect below owns this snapshot. Initializing
  // from render data would let an abandoned render change which Assets a
  // pending copy operation is allowed to publish against.
  const copyAssetsRef = useRef<readonly AssetSummary[]>([]);
  const orderedAssets = useMemo(
    () =>
      assetsData
        ? getOrderedLibraryAssets(assetsData.assets, { sort, media, status })
        : [],
    [assetsData, sort, media, status],
  );
  // Ticket 11: single Library search bar with instant local filtering and
  // explicit semantic submit. Local preserves the ordered input (sort order
  // retained, Pending/Failed included). Semantic composes raw SearchAsset
  // projections through ticket 05's helper, preserves relevance order, and
  // excludes non-Indexed Assets. Raw scores appear only in advanced details.
  // Ticket 12: image and similar reuse the same composed, cancellable
  // waterfall in relevance/similarity order with shared latest-wins.
  // These memos stay above the pending/error early returns to keep hook order
  // stable.
  const effectiveMode: LibraryResultMode =
    resultMode ?? (query.trim() !== "" ? { kind: "local", query } : { kind: "browse" });
  const isSemanticMode = effectiveMode.kind === "semantic";
  const isLocalMode = effectiveMode.kind === "local";
  const isImageMode = effectiveMode.kind === "image";
  const isSimilarMode = effectiveMode.kind === "similar";
  const semanticModeQuery = isSemanticMode ? effectiveMode.query : "";
  const localDisplayQuery = isLocalMode ? effectiveMode.query : query;
  const summaryMap = useMemo(
    () => buildAssetSummaryMap(assetsData?.assets ?? []),
    [assetsData],
  );
  const localMatches = useMemo(
    () => filterLocalAssets(orderedAssets, localDisplayQuery),
    [orderedAssets, localDisplayQuery],
  );
  // Keep the raw local signal separate from the displayed wall. A zero raw
  // match is a genuine no-match state; a positive raw count with no visible
  // item means Media/Status filters hid the matches and needs recovery copy.
  const unfilteredLocalMatches = useMemo(
    () => filterLocalAssets(assetsData?.assets ?? [], localDisplayQuery),
    [assetsData, localDisplayQuery],
  );
  const unfilteredLocalMatchCount = unfilteredLocalMatches.length;
  const isSearching = search.current.status === "loading";
  const searchError = search.current.error;
  // Retrieval modes never reinterpret their meaning/image labels as a
  // filename query while work is pending or unavailable. The active filters
  // and sort already live in orderedAssets, so keep that browse wall intact.
  const retrievalFallbackAssets = orderedAssets;
  const copyResultIdentity = effectiveMode.kind === "browse"
    ? ""
    : effectiveMode.kind === "local" || effectiveMode.kind === "semantic"
      ? effectiveMode.query
      : effectiveMode.kind === "image"
        ? effectiveMode.selectionId
        : effectiveMode.assetId;
  useLayoutEffect(() => {
    copyAssetsRef.current = assetsData?.assets ?? [];
    invalidateCopyOperation();
  }, [assetsData, copyResultIdentity, effectiveMode.kind, invalidateCopyOperation, media, query, selectedAssetId, selectedIds, sort, status]);
  useEffect(() => () => {
    copyGenerationRef.current += 1;
    copyOperationRef.current = null;
  }, []);
  const searchRetryable = search.current.status === "error" && search.current.retryable;
  const hasResults = search.result !== null;
  const visualImageLabel = search.current.request?.kind === "image"
    ? search.current.request.label
    : imageQueryLabel ?? "selected image";
  const similarSource = isSimilarMode ? summaryMap.get(effectiveMode.assetId) : undefined;
  const visualSimilarLabel = similarSource
    ? getAssetDisplayName(similarSource)
    : isSimilarMode
      ? effectiveMode.assetId
      : "selected Asset";
  const retryFocusGenerationRef = useRef(0);
  const retryFocusFrameCancelRef = useRef<(() => void) | null>(null);
  useEffect(() => () => {
    retryFocusGenerationRef.current += 1;
    retryFocusFrameCancelRef.current?.();
    retryFocusFrameCancelRef.current = null;
  }, []);
  useEffect(() => {
    retryFocusGenerationRef.current += 1;
    retryFocusFrameCancelRef.current?.();
    retryFocusFrameCancelRef.current = null;
  }, [query, resultMode]);
  const focusSearchAfterRetry = (source: HTMLElement | null) => {
    const generation = ++retryFocusGenerationRef.current;
    retryFocusFrameCancelRef.current?.();
    retryFocusFrameCancelRef.current = scheduleFocusRestoration(() => {
      retryFocusFrameCancelRef.current = null;
      if (generation !== retryFocusGenerationRef.current) return;
      const active = document.activeElement;
      if (active instanceof HTMLElement && active !== document.body && active !== source) return;
      const target = document.getElementById("library-search-input");
      if (isRestorableFocusTarget(target)) target.focus({ preventScroll: true });
    });
  };
  const retrySearch = () => {
    const source = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    onRetrySearch?.();
    focusSearchAfterRetry(source);
  };
  const composedResults = useMemo(
    () => search.result ? composeSearchItems(search.result.assets, summaryMap) : null,
    [search.result, summaryMap],
  );
  const indexedItems = useMemo(
    () => {
      if (!composedResults) return [];
      const activeAssetIds = new Set(orderedAssets.map((asset) => asset.asset_id));
      return composedResults.items.filter(
        (item) => item.summary.status === "indexed" && activeAssetIds.has(item.summary.asset_id),
      );
    },
    [composedResults, orderedAssets],
  );
  const unfilteredIndexedCount = composedResults?.items.filter(
    (item) => item.summary.status === "indexed",
  ).length ?? 0;
  const allResultsFiltered = unfilteredIndexedCount > 0 && indexedItems.length === 0;
  const searchSummaries = useMemo(() => indexedItems.map((item) => item.summary), [indexedItems]);
  const staleCount = composedResults?.stale.length ?? 0;
  const retrievalView = isSemanticMode
    ? {
        label: "Semantic",
        kind: "semantic",
        title: isSearching && !hasResults
          ? `Searching the Active Index Recipe for “${semanticModeQuery}”…`
          : `Semantic results for “${semanticModeQuery}” · ${searchSummaries.length}`,
        context: null,
        order: "Semantic results in relevance order · only Indexed Assets appear here · raw scores stay in advanced details.",
        pending: <>Searching the Active Index Recipe for &ldquo;{semanticModeQuery}&rdquo;&hellip;</>,
        errorTitle: "Semantic search unavailable",
        retryLabel: "Retry search",
        canRetry: true,
        cannotRetry: "This request cannot be retried. Clear search to return to browsing.",
        emptyTitle: <>No semantic matches for &ldquo;{semanticModeQuery}&rdquo;</>,
        emptyCopy: <>Only Indexed Assets appear in semantic results. Try a different description, or index more Assets with the Active Index Recipe.</>,
        fallbackContext: null,
        actionsClassName: undefined,
      }
    : isImageMode
      ? {
          label: "Image",
          kind: "image",
          title: isSearching && !hasResults
            ? "Searching the Active Index Recipe for the chosen image…"
            : `Image results · ${searchSummaries.length}`,
          context: <>Query image: &ldquo;{visualImageLabel}&rdquo;</>,
          order: "Image results in relevance order · only Indexed Assets appear here · raw scores stay in advanced details.",
          pending: <>Searching the Active Index Recipe for the chosen image&hellip;</>,
          errorTitle: "Image search unavailable",
          retryLabel: "Retry image search",
          canRetry: imageSelectionAvailable,
          cannotRetry: "This request cannot be retried. Choose another image or clear search.",
          emptyTitle: <>No image matches</>,
          emptyCopy: <>Query image: &ldquo;{visualImageLabel}&rdquo;. Only Indexed Assets appear in image results. Try another image, or index more Assets with the Active Index Recipe.</>,
          fallbackContext: <>Query image: &ldquo;{visualImageLabel}&rdquo;. </>,
          actionsClassName: "import-actions",
        }
      : isSimilarMode
        ? {
            label: "Similar",
            kind: "similar",
            title: isSearching && !hasResults
              ? "Finding similar Assets…"
              : `Similar results · ${searchSummaries.length}`,
            context: <>Source Asset: &ldquo;{visualSimilarLabel}&rdquo;</>,
            order: "Similar results in similarity order · only Indexed Assets appear here · raw scores stay in advanced details.",
            pending: <>Finding similar Assets in the Active Index Recipe&hellip;</>,
            errorTitle: "Find Similar unavailable",
            retryLabel: "Retry similar search",
            canRetry: true,
            cannotRetry: "This request cannot be retried. Clear search to return to browsing.",
            emptyTitle: <>No similar Assets</>,
            emptyCopy: <>Source Asset: &ldquo;{visualSimilarLabel}&rdquo;. Only Indexed Assets appear in similar results. Index more Assets with the Active Index Recipe to expand this search.</>,
            fallbackContext: <>Source Asset: &ldquo;{visualSimilarLabel}&rdquo;. </>,
            actionsClassName: "import-actions",
          }
        : null;

  // Ticket 10: prune checkbox selection when the Asset list no longer
  // contains an ID (e.g. the inspector deleted it via its own mutation that
  // updated the ["assets"] cache). This keeps toolbar/inspector coherent
  // without lifting selection state to the Library page.
  useEffect(() => {
    if (!assetsData) return;
    const present = new Set(assetsData.assets.map((item) => item.asset_id));
    setSelectedIds((current) => {
      if ([...current].every((id) => present.has(id))) return current;
      return new Set([...current].filter((id) => present.has(id)));
    });
  }, [assetsData]);

  if (assetsQuery.isPending) return <p role="status" aria-label="Loading Assets">Loading Assets…</p>;
  if (assetsQuery.isError) return <section className="notice notice-warning" role="alert" aria-label="Could not load Assets"><strong>Could not load Assets</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void assetsQuery.refetch()}>Retry Assets</button></section>;

  const { assets } = assetsQuery.data;
  // Ticket 17: selection lives only in this `useState` (never in URL or
  // persisted preferences). The waterfall hover checkbox adds/removes IDs via
  // `toggleAsset`; the toolbar below renders only for >=1 selection.
  const toggleAsset = (assetId: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(assetId)) next.delete(assetId);
    else next.add(assetId);
    return next;
  });
  // Ticket 17: stable visual-order IDs for batch actions. The visible
  // waterfall order wins (browse => sorted input, search modes => composed
  // relevance order or local fallback); selected IDs absent from the visible
  // wall (e.g. filtered out) append in sorted-library order so none are
  // dropped and the order stays deterministic.
  const getOrderedSelectedIds = (): string[] => {
    if (!selectedIds.size) return [];
    const visibleForOrder: readonly AssetSummary[] = (() => {
      if (isLocalMode) return localMatches;
      if (isSemanticMode || isImageMode || isSimilarMode) {
        if (hasResults && searchSummaries.length) return searchSummaries;
        return orderedAssets;
      }
      return orderedAssets;
    })();
    const visibleOrder = new Map(visibleForOrder.map((item, index) => [item.asset_id, index] as const));
    const inVisible = [...selectedIds].filter((id) => visibleOrder.has(id));
    inVisible.sort((a, b) => (visibleOrder.get(a) ?? 0) - (visibleOrder.get(b) ?? 0));
    if (inVisible.length === selectedIds.size) return inVisible;
    const libraryOrder = new Map(orderedAssets.map((item, index) => [item.asset_id, index] as const));
    const missing = [...selectedIds].filter((id) => !visibleOrder.has(id));
    missing.sort((a, b) => (libraryOrder.get(a) ?? Number.MAX_SAFE_INTEGER) - (libraryOrder.get(b) ?? Number.MAX_SAFE_INTEGER));
    return [...inVisible, ...missing];
  };
  const clearSelection = () => {
    setSelectedIds(new Set());
  };
  const beginCopyOperation = (assetIds: readonly string[]): CopyOperation => {
    const operation: CopyOperation = {
      generation: copyGenerationRef.current + 1,
      assetIds: [...assetIds],
    };
    copyGenerationRef.current = operation.generation;
    copyOperationRef.current = operation;
    setCopyPending(true);
    setCopyNotice(null);
    return operation;
  };
  const isCurrentCopyOperation = (operation: CopyOperation): boolean => {
    if (
      copyOperationRef.current !== operation ||
      copyGenerationRef.current !== operation.generation
    ) return false;
    const currentAssetIds = new Set(copyAssetsRef.current.map((asset) => asset.asset_id));
    return operation.assetIds.every((assetId) => currentAssetIds.has(assetId));
  };
  const finishCopyOperation = (operation: CopyOperation) => {
    if (!isCurrentCopyOperation(operation)) return;
    copyOperationRef.current = null;
    setCopyPending(false);
  };
  // Ticket 17: one selection uses the single-file client method; multiple
  // selections call the multi-file method once with stable visual-order IDs
  // (ID-only, never paths). Selection is preserved on success and failure.
  const runCopyOriginalFiles = async () => {
    const assetIds = getOrderedSelectedIds();
    if (!assetIds.length || copyPending || mutation.isPending) return;
    const operation = beginCopyOperation(assetIds);
    try {
      if (assetIds.length === 1) {
        await client.copyOriginalFile(assetIds[0]);
      } else {
        await client.copyOriginalFiles(assetIds);
      }
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({
          kind: "success",
          text: assetIds.length === 1 ? "Original file reference copied." : `Copied ${assetIds.length} original file references.`,
        });
      }
    } catch (error) {
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({
          kind: "error",
          text: tauriErrorDetail(error, "Copy original files failed. The Library was not modified."),
        });
      }
    } finally {
      finishCopyOperation(operation);
    }
  };
  // Ticket 01 follow-up: card right-click menu actions. "Copy image" is the
  // primary Clipboard Copy (GIFs paste animated via CF_HDROP, stills as
  // image previews via CF_DIBV5/PNG); "Copy original file" is the raw
  // Library Copy reference. Both are ID-only and report through copyNotice.
  const runCardClipboardCopy = async (assetId: string) => {
    if (copyPending || mutation.isPending) return;
    const operation = beginCopyOperation([assetId]);
    try {
      await client.copyAssetToClipboard(assetId);
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({ kind: "success", text: "Copied to clipboard. Paste into QQ or WeChat." });
      }
    } catch (error) {
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({ kind: "error", text: tauriErrorDetail(error, "Clipboard Copy failed. The Library was not modified. Use Reveal in Explorer to locate the file.") });
      }
    } finally {
      finishCopyOperation(operation);
    }
  };
  const runCardCopyOriginal = async (assetId: string) => {
    if (copyPending || mutation.isPending) return;
    const operation = beginCopyOperation([assetId]);
    try {
      await client.copyOriginalFile(assetId);
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({ kind: "success", text: "Original file reference copied." });
      }
    } catch (error) {
      if (isCurrentCopyOperation(operation)) {
        setCopyNotice({ kind: "error", text: tauriErrorDetail(error, "Copy original file failed. The Library was not modified.") });
      }
    } finally {
      finishCopyOperation(operation);
    }
  };
  const requestBatch = (action: "delete" | "rebuild-active-index") => {
    const assetIds = getOrderedSelectedIds();
    if (!assetIds.length) return;
    setConfirmation(action === "delete"
      ? { title: `Delete ${assetIds.length} selected Asset(s)?`, detail: "This deletes each Asset's Library Copy and Derived Artifacts. This cannot be undone.", confirmLabel: "Delete selected Assets", request: { action, assetIds } }
      : { title: `Rebuild ${assetIds.length} selected Asset(s)?`, detail: "This clears their active-recipe embeddings and queues new indexing work. Running Asset jobs are skipped.", confirmLabel: "Queue rebuild", request: { action, assetIds } });
  };
  const confirmBatch = () => {
    if (!confirmation) return;
    if (confirmation.request.action === "rebuild-active-index" && indexingBlocked) {
      setConfirmation(null);
      setLibraryNotice({
        kind: "error",
        text: "Indexing authorization was lost. Run the Runtime health check before rebuilding the Active Index.",
      });
      return;
    }
    mutation.mutate(confirmation.request);
  };
  const waterfallProps = {
    density,
    checkedIds: selectedIds,
    onOpenAsset: onSelectAsset,
    onToggleChecked: toggleAsset,
    onFindSimilar,
    onCopyImage: runCardClipboardCopy,
    onCopyOriginal: runCardCopyOriginal,
    copyBusy: copyPending,
    sectionRef: wallRef,
    accepting: Boolean(dragPreview),
  };

  return <>
    <section className="asset-toolbar"><div className="asset-toolbar-actions"><span className="toolbar-hint">Drag image files or folders onto the asset wall to import</span></div></section>
    {selectedIds.size > 0 ? (
      <section className="selection-toolbar" role="toolbar" aria-label="Selection toolbar">
        <span>{selectedIds.size} selected</span>
        <div className="selection-toolbar-actions">
          <button className="button button-secondary" type="button" disabled={copyPending || mutation.isPending} onClick={() => void runCopyOriginalFiles()}>Copy original files</button>
          <button className="button button-secondary" type="button" disabled={mutation.isPending || indexingBlocked} onClick={() => requestBatch("rebuild-active-index")}>Rebuild Active Index</button>
          <button className="button button-danger" type="button" disabled={mutation.isPending} onClick={() => requestBatch("delete")}>Delete selected</button>
          <button className="button button-secondary" type="button" disabled={copyPending || mutation.isPending} onClick={clearSelection}>Clear selection</button>
        </div>
      </section>
    ) : null}
    {indexingBlocked ? <p role="note">Indexing is unavailable until the current session passes the Runtime health check. Browsing, selection, and delete still work.</p> : null}
    {feedback ? <section className="notice notice-success" role="status"><span>{feedback}</span></section> : null}
    {copyNotice ? <section className={`notice ${copyNotice.kind === "error" ? "notice-warning" : "notice-success"}`} role={copyNotice.kind === "error" ? "alert" : "status"}><span>{copyNotice.text}</span></section> : null}
    {libraryNotice ? <section className={`notice ${libraryNotice.kind === "error" ? "notice-warning" : "notice-success"}`} role={libraryNotice.kind === "error" ? "alert" : "status"}><span>{libraryNotice.text}</span></section> : null}
    {!isLocalMode && !isSemanticMode && !isImageMode && !isSimilarMode && assets.length ? (
      <BrowseResultContext total={assets.length} visible={orderedAssets.length} media={media} status={status} />
    ) : null}
    {isLocalMode ? (
      <section className="notice" role="status" aria-label="Local search results">
        <strong>Local matches for &ldquo;{localDisplayQuery}&rdquo; &middot; {localMatches.length} of {orderedAssets.length}</strong>
        <span>Local matches &middot; instant filter by displayed name and available/primary Source Path &middot; includes Pending and Failed Assets.</span>
        {onClearSearch ? (
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => onClearSearch()}>
              Clear search
            </button>
          </div>
        ) : null}
      </section>
    ) : null}
    {retrievalView ? (
      <section
        className="notice"
        role="status"
        aria-live={searchError || staleCount ? "off" : "polite"}
        aria-label={`${retrievalView.label} search results`}
      >
        <strong>{retrievalView.title}</strong>
        {retrievalView.context ? <span>{retrievalView.context}</span> : null}
        <span>{retrievalView.order}</span>
        {isSearching && hasResults ? <span>{retrievalView.pending}</span> : null}
        {searchError ? (
          <section className="notice notice-warning" role="alert" aria-label={`${retrievalView.label} search error`}>
            <strong>{retrievalView.errorTitle}</strong>
            <span>{searchError}</span>
            {retrievalView.context ? <span>{retrievalView.context}</span> : null}
            <span>Library browsing remains available with the active Media and Status filters below.</span>
            {searchRetryable && retrievalView.canRetry && onRetrySearch ? <button className="button button-secondary" type="button" onClick={retrySearch}>{retrievalView.retryLabel}</button> : null}
            {isImageMode && onChooseImage && (!searchRetryable || !retrievalView.canRetry) ? <button className="button button-secondary" type="button" onClick={onChooseImage}>Choose another image</button> : null}
            {!searchRetryable ? <span>{retrievalView.cannotRetry}</span> : null}
          </section>
        ) : null}
        {staleCount ? (
          <section className="notice notice-warning" role="status" aria-label={`Stale ${retrievalView.kind} results`}>
            <strong>{staleCount} {retrievalView.kind} result{staleCount === 1 ? "" : "s"} omitted</strong>
            <span>Their Assets are no longer in the current Asset list. Nothing was rendered with invented dimensions or Source Records.</span>
          </section>
        ) : null}
        {hasResults && indexedItems.length ? <AdvancedDetails items={indexedItems} /> : null}
        {onClearSearch ? (
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => onClearSearch()}>
              Clear search
            </button>
          </div>
        ) : null}
      </section>
    ) : null}
    <div className="asset-wall">
      {!assets.length ? (
        <div className={`import-drop-card${dragPreview ? " import-drop-card-accepting" : ""}`} aria-label="Import drop card" ref={wallRef}>
          <h2>Drag image files or folders here</h2>
          <p>Release them over this card to start an Import Batch. Sources are scanned and validated before anything is written to the Library.</p>
          <p className="import-drop-card-hint">Keyboard alternative: use Import in the Library toolbar to choose files or a folder.</p>
        </div>
      ) : isLocalMode ? (
        localMatches.length ? (
          <AssetWaterfall assets={localMatches} {...waterfallProps} />
        ) : unfilteredLocalMatchCount > 0 ? (
          <FilteredRetrievalEmpty label="Local" count={unfilteredLocalMatchCount} media={media} status={status} onClearFilters={onClearFilters} onClearSearch={onClearSearch} wallRef={wallRef} />
        ) : (
          <div className="empty-state" aria-label="No local matches" ref={wallRef}>
            <h2>No local matches for &ldquo;{localDisplayQuery}&rdquo;</h2>
            <p>
              The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
              Local matching covers displayed names and the available/primary Source Path only.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
              <ActiveFilterRecovery media={media} status={status} onClearFilters={onClearFilters} />
            </div>
          </div>
        )
      ) : retrievalView ? (
        hasResults && searchSummaries.length ? (
          <AssetWaterfall assets={searchSummaries} {...waterfallProps} />
        ) : hasResults && allResultsFiltered && !isSearching && !searchError ? (
          <FilteredRetrievalEmpty label={retrievalView.label} count={unfilteredIndexedCount} media={media} status={status} onClearFilters={onClearFilters} onClearSearch={onClearSearch} wallRef={wallRef} />
        ) : hasResults && !searchSummaries.length && !isSearching && !searchError ? (
          <div className="empty-state" aria-label={`No ${retrievalView.kind} matches`} ref={wallRef}>
            <h2>{retrievalView.emptyTitle}</h2>
            <p>{retrievalView.emptyCopy}</p>
            <div className={retrievalView.actionsClassName}>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
              <ActiveFilterRecovery media={media} status={status} onClearFilters={onClearFilters} />
            </div>
          </div>
        ) : retrievalFallbackAssets.length ? (
          <AssetWaterfall assets={retrievalFallbackAssets} {...waterfallProps} />
        ) : (
          <div className="empty-state" aria-label="No filtered Assets" ref={wallRef}>
            <h2>No Assets match these filters</h2>
            <p>
              {retrievalView.fallbackContext}
              The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
              Adjust the Media and Status filters, or clear them to browse the full Library.
            </p>
            <div className={retrievalView.actionsClassName}>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
              <ActiveFilterRecovery media={media} status={status} onClearFilters={onClearFilters} />
            </div>
          </div>
        )
      ) : orderedAssets.length ? (
        <AssetWaterfall assets={orderedAssets} {...waterfallProps} />
      ) : (
        <div className="empty-state" aria-label="No filtered Assets" ref={wallRef}>
          <h2>No Assets match these filters</h2>
          <p>
            The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
            Adjust the Media and Status filters, or clear them to browse the full Library.
          </p>
          <div>
            <button className="button button-secondary" type="button" onClick={() => onClearFilters?.()}>
              Clear filters
            </button>
          </div>
        </div>
      )}
      {dragPreview ? (
        <div className="drop-cue" role="status" aria-live="polite" aria-label="Release to import">
          <strong>Release to import</strong>
          <span>{dragPreview.itemCount} item{dragPreview.itemCount === 1 ? "" : "s"} ready</span>
        </div>
      ) : null}
    </div>
    {confirmation ? (
      <ConfirmDialog
        titleId="confirm-title"
        title={confirmation.title}
        detail={confirmation.detail}
        confirmLabel={confirmation.confirmLabel}
        pending={mutation.isPending}
        onCancel={() => setConfirmation(null)}
        onConfirm={confirmBatch}
        onEscape={() => setConfirmation(null)}
      />
    ) : null}
  </>;
}
