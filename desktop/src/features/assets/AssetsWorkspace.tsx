import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { mediaUrl } from "../../api/media-url";
import { tauriErrorDetail } from "../../api/tauri-error";
import {
  subscribeNativeDrag,
  type NativeDragSubscribe,
} from "../../api/native-drag";
import { useImportBatch } from "../import/ImportBatchContext";
import { ImportFailureDetails } from "../import/ImportFailureDetails";
import { useOptionalRuntimeHealth } from "../runtime/RuntimeHealthProvider";
import { AssetWaterfall } from "./AssetWaterfall";
import { buildAssetSummaryMap, composeSearchItems } from "../../api/result-models";
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
import type { AssetDetail, AssetListResult, AssetSummary, SearchAsset } from "../../api/types";

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
  /** Raw semantic projections for the latest committed request (null until first success). */
  semanticRawResults?: SearchAsset[] | null;
  /** Query text of the latest committed semantic results (null until first success). */
  semanticQuery?: string | null;
  /** Raw image projections for the latest committed image request (null until first success). */
  imageRawResults?: SearchAsset[] | null;
  /** Request ID of the latest committed image results (null until first success). */
  committedImageRequestId?: string | null;
  /** Raw similar projections for the latest committed similar request (null until first success). */
  similarRawResults?: SearchAsset[] | null;
  /** Asset ID of the latest committed similar results (null until first success). */
  committedSimilarAssetId?: string | null;
  /** True while a Search Request (any mode) is in flight. */
  isSearching?: boolean;
  /** Latest error for the current request (null when none). Only the latest identity may commit. */
  searchError?: string | null;
  /** Clear all search modes and restore browsing (cancels active work). */
  onClearSearch?: () => void;
  /**
   * Find Similar entry point for ticket 12 (card hover/context action).
   * Shares the same result mode as the inspector entry point.
   */
  onFindSimilar?: (assetId: string) => void;
}

type MutationRequest =
  | { kind: "delete-asset"; assetId: string }
  | { kind: "remove-source"; assetId: string; sourcePath: string }
  | { kind: "batch"; action: "delete" | "rebuild-active-index"; assetIds: string[] };

type LibraryNotice = { kind: "error" | "success"; text: string };

interface ConfirmAction {
  title: string;
  detail: string;
  confirmLabel: string;
  request: MutationRequest;
}

function assetName(asset: AssetSummary): string {
  return getAssetDisplayName(asset);
}

function dimensions(asset: AssetSummary): string {
  return asset.width && asset.height ? `${asset.width} × ${asset.height}` : "Dimensions unavailable";
}

function statusLabel(status: AssetSummary["status"]): string {
  return `${status.charAt(0).toUpperCase()}${status.slice(1)} Asset`;
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="detail-section"><h3>{title}</h3>{children}</section>;
}

function AssetDetailContent({ asset, onDeleteAsset, onRevealManaged, onRemoveSourceRecord, onRevealSource, mutating, revealing }: { asset: AssetDetail; onDeleteAsset: () => void; onRevealManaged: () => void; onRemoveSourceRecord: (sourcePath: string) => void; onRevealSource: (sourcePath: string) => void; mutating: boolean; revealing: boolean }) {
  const preview = mediaUrl(asset.library_url);
  return (
    <div className="detail-content">
      <div className="detail-hero">
        {preview ? <img className="detail-media" src={preview} alt={`${assetName(asset)} preview`} /> : <div className="detail-media media-placeholder" />}
        <div>
          <span className={`status-pill status-${asset.status}`}>{statusLabel(asset.status)}</span>
          <h3>{assetName(asset)}</h3>
          <p>{dimensions(asset)} · {asset.media_type} · {asset.source_record_count} Source Record{asset.source_record_count === 1 ? "" : "s"}</p>
          <button className="button button-secondary" type="button" disabled={revealing} onClick={onRevealManaged}>Reveal Managed File</button>
          <button className="button button-danger" type="button" disabled={mutating} onClick={onDeleteAsset}>Delete Asset</button>
        </div>
      </div>
      <div className="detail-sections">
        <DetailSection title="Index recipes">
          <p>Active recipes: {asset.indexed_recipe_labels.join(", ") || "None yet"}</p>
          {asset.stale_recipe_labels.length ? <p>Stale recipes: {asset.stale_recipe_labels.join(", ")}</p> : null}
        </DetailSection>
        <DetailSection title="Source Records">
          {asset.source_records.length ? <ul className="detail-list">
            {asset.source_records.map((source) => <li key={source.source_path}><span className="mono">{source.source_path}</span><button className="text-button detail-action" type="button" disabled={revealing} onClick={() => onRevealSource(source.source_path)}>Reveal Source</button><button className="text-button detail-action" type="button" disabled={mutating} onClick={() => onRemoveSourceRecord(source.source_path)}>Remove Source Record</button></li>)}
          </ul> : <p>No Source Records are available.</p>}
        </DetailSection>
        <DetailSection title="OCR">
          {asset.ocr_results.length ? <ul className="detail-list">{asset.ocr_results.map((result) => <li key={result.result_id}>{result.text || "OCR result contains no text."}</li>)}</ul> : <p>No OCR text is available for this Asset.</p>}
        </DetailSection>
        <DetailSection title="Jobs">
          {asset.jobs.length ? <ul className="detail-list">{asset.jobs.map((job) => <li key={job.job_id}>{job.type} · {job.status} · attempt {job.attempt_count}</li>)}</ul> : <p>No jobs are recorded for this Asset.</p>}
        </DetailSection>
      </div>
    </div>
  );
}

/**
 * Legacy centered Asset detail dialog (pre-ticket-10).
 *
 * Retained only for legacy routes until ticket 19 removes it. The Library
 * route now uses the right-side `AssetInspector` (non-overlaying `aside`)
 * opened from `asset=<asset-id>`; this dialog must not be rendered on `/`.
 */
export function LegacyAssetDetailDialog({ assetId, client, onClose, onDeleteAsset, onRevealManaged, onRemoveSourceRecord, onRevealSource, mutating, revealing }: { assetId: string; client: MemeSortClient; onClose: () => void; onDeleteAsset: () => void; onRevealManaged: () => void; onRemoveSourceRecord: (sourcePath: string) => void; onRevealSource: (sourcePath: string) => void; mutating: boolean; revealing: boolean }) {
  const detailQuery = useQuery({ queryKey: ["asset-detail", assetId], queryFn: () => client.getAssetDetail(assetId) });
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="dialog detail-dialog" role="dialog" aria-modal="true" aria-labelledby="asset-detail-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-title-row"><div><p className="eyebrow">Asset detail</p><h2 id="asset-detail-title">Asset details</h2></div><button className="button button-secondary" type="button" autoFocus onClick={onClose}>Close</button></div>
        {detailQuery.isPending ? <p aria-live="polite">Loading Asset detail…</p> : null}
        {detailQuery.isError ? <section className="notice notice-warning" role="alert"><strong>Asset details are unavailable</strong><span>{tauriErrorDetail(detailQuery.error, "This Asset may no longer exist in the Library. Refresh the Asset wall and try again.")}</span></section> : null}
        {detailQuery.data ? <AssetDetailContent asset={detailQuery.data.asset} onDeleteAsset={onDeleteAsset} onRevealManaged={onRevealManaged} onRemoveSourceRecord={onRemoveSourceRecord} onRevealSource={onRevealSource} mutating={mutating} revealing={revealing} /> : null}
      </section>
    </div>
  );
}

// Backwards-compatible alias for any legacy-route import until ticket 19.
export const AssetDetailDialog = LegacyAssetDetailDialog;

function ConfirmDialog({ action, onCancel, onConfirm, pending }: { action: ConfirmAction; onCancel: () => void; onConfirm: () => void; pending: boolean }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !pending) onCancel(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onCancel, pending]);
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={pending ? undefined : onCancel}>
      <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(event) => event.stopPropagation()}>
        <p className="eyebrow">Confirm change</p><h2 id="confirm-title">{action.title}</h2><p>{action.detail}</p>
        <div className="dialog-actions"><button className="button button-secondary" type="button" disabled={pending} onClick={onCancel}>Cancel</button><button className="button button-danger" type="button" autoFocus disabled={pending} onClick={onConfirm}>{pending ? "Working…" : action.confirmLabel}</button></div>
      </section>
    </div>
  );
}

function mutationSummary(request: MutationRequest, result: Awaited<ReturnType<MemeSortClient["deleteAsset"]>> | Awaited<ReturnType<MemeSortClient["batchAssetAction"]>>): string {
  if (request.kind === "batch" && "affected_asset_ids" in result) {
    return request.action === "delete"
      ? `Deleted ${result.affected_asset_ids.length} Asset(s).`
      : `Queued ${result.reindex_jobs_created} Active Index rebuild(s); skipped ${result.skipped_running_asset_ids.length} running Asset(s).`;
  }
  if (request.kind === "remove-source" && "asset_deleted" in result) return result.asset_deleted ? "Removed the final Source Record and deleted the Orphan Asset." : "Removed the Source Record.";
  return "Deleted the Asset and its Library Copy and Derived Artifacts.";
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
  semanticRawResults = null,
  semanticQuery = null,
  imageRawResults = null,
  committedImageRequestId = null,
  similarRawResults = null,
  committedSimilarAssetId = null,
  isSearching = false,
  searchError = null,
  onClearSearch,
  onFindSimilar,
}: AssetsWorkspaceProps) {
  const queryClient = useQueryClient();
  const importBatch = useImportBatch();
  const runtimeHealth = useOptionalRuntimeHealth();
  const indexingBlocked = runtimeHealth?.isBlocked ?? false;
  const startBatch = importBatch.startBatch;
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [confirmation, setConfirmation] = useState<ConfirmAction | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [libraryNotice, setLibraryNotice] = useState<LibraryNotice | null>(null);
  // Ticket 17: batch Copy original files feedback. Selection is preserved on
  // both success and failure; failure never claims clipboard restoration.
  const [copyPending, setCopyPending] = useState(false);
  const [copyNotice, setCopyNotice] = useState<LibraryNotice | null>(null);
  const [dragPreview, setDragPreview] = useState<{ itemCount: number } | null>(null);
  const wallRef = useRef<HTMLDivElement | null>(null);
  const startedDropIdRef = useRef<string | null>(null);
  const nativeDragSubscribe = nativeDrag ?? subscribeNativeDrag;
  const assetsQuery = useQuery({ queryKey: ["assets"], queryFn: () => client.getAssets() });
  const mutation = useMutation({
    mutationFn: async (request: MutationRequest) => {
      if (request.kind === "delete-asset") return client.deleteAsset(request.assetId);
      if (request.kind === "remove-source") return client.removeSourceRecord(request.assetId, request.sourcePath);
      return client.batchAssetAction(request.action, request.assetIds);
    },
    onSuccess: async (result, request) => {
      setFeedback(mutationSummary(request, result));
      // Ticket 10: keep inspector/selection coherent after deletes performed
      // from the toolbar. Batch deletes reconcile from the mutation response;
      // single deletes (legacy path) close only their own inspector.
      // Ticket 17: only Delete reconciles selection (removing exactly
      // `affected_asset_ids` so skipped/failed IDs are retained). Rebuild
      // preserves the full selection and never closes the inspector because
      // the Assets still exist.
      if (request.kind === "batch" && "affected_asset_ids" in result) {
        const affected = new Set(result.affected_asset_ids);
        if (request.action === "delete") {
          setSelectedIds((current) => {
            const next = new Set([...current].filter((id) => !affected.has(id)));
            return next;
          });
          // Optimistically drop deleted Assets from the visible wall so the UI
          // reflects the deletion even when the mocked `getAssets` still
          // returns the pre-delete list (tests) or before refetch settles.
          if (affected.size) {
            queryClient.setQueryData<AssetListResult | undefined>(
              ["assets"],
              (old) =>
                old
                  ? { ...old, assets: old.assets.filter((item) => !affected.has(item.asset_id)) }
                  : old,
            );
            for (const deletedId of affected) {
              queryClient.removeQueries({ queryKey: ["asset-detail", deletedId] });
            }
          }
          if (selectedAssetId && affected.has(selectedAssetId)) {
            onCloseDetail();
          }
        }
        // Rebuild: intentionally preserve selection and keep the inspector
        // open; only feedback + invalidation below apply.
      } else {
        const deletedId = request.kind === "delete-asset" ? request.assetId : null;
        if (deletedId) {
          setSelectedIds((current) => {
            if (!current.has(deletedId)) return current;
            const next = new Set(current);
            next.delete(deletedId);
            return next;
          });
          queryClient.setQueryData<AssetListResult | undefined>(
            ["assets"],
            (old) =>
              old
                ? { ...old, assets: old.assets.filter((item) => item.asset_id !== deletedId) }
                : old,
          );
          queryClient.removeQueries({ queryKey: ["asset-detail", deletedId] });
        }
        // Legacy single-asset path closes its own inspector; inspector-owned
        // deletes (ticket 10) close themselves via onDeleted/onClose.
        if (deletedId && deletedId === selectedAssetId) {
          onCloseDetail();
        }
        if (request.kind === "remove-source" && "asset_deleted" in result && result.asset_deleted) {
          onCloseDetail();
        }
      }
      setConfirmation(null);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["assets"] }), queryClient.invalidateQueries({ queryKey: ["app-state"] })]);
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
        await startBatch(() => client.startLibraryImport(dropId));
        setLibraryNotice({ kind: "success", text: `Import Batch started for ${label}.` });
        await queryClient.invalidateQueries({ queryKey: ["app-state"] });
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
  const orderedAssets = useMemo(
    () =>
      assetsData
        ? getOrderedLibraryAssets(assetsData.assets, { sort, media, status })
        : [],
    [assetsData, sort, media, status],
  );
  const isFiltered = media !== DEFAULT_LIBRARY_MEDIA || status !== DEFAULT_LIBRARY_STATUS;

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
  const imageSelectionId = isImageMode ? effectiveMode.selectionId : null;
  const similarModeAssetId = isSimilarMode ? effectiveMode.assetId : "";
  const localDisplayQuery = isLocalMode ? effectiveMode.query : query;
  const summaryMap = useMemo(
    () => buildAssetSummaryMap(assetsData?.assets ?? []),
    [assetsData],
  );
  const localMatches = useMemo(
    () => filterLocalAssets(orderedAssets, localDisplayQuery),
    [orderedAssets, localDisplayQuery],
  );
  const composedSemantic = useMemo(
    () => (semanticRawResults ? composeSearchItems(semanticRawResults, summaryMap) : null),
    [semanticRawResults, summaryMap],
  );
  const composedImage = useMemo(
    () => (imageRawResults ? composeSearchItems(imageRawResults, summaryMap) : null),
    [imageRawResults, summaryMap],
  );
  const composedSimilar = useMemo(
    () => (similarRawResults ? composeSearchItems(similarRawResults, summaryMap) : null),
    [similarRawResults, summaryMap],
  );
  // Only the committed results for the current query may render.
  // Older in-flight promises settle with a mismatched identity and stay hidden
  // (latest-wins, including cross-mode); typing a new q falls back to local
  // via ticket 07's effect. Image/similar are query-independent and stay
  // sticky until cleared (never restored from URL/storage).
  const hasFreshSemantic =
    isSemanticMode && semanticRawResults !== null && semanticQuery !== null && semanticQuery === semanticModeQuery;
  const hasFreshImage =
    isImageMode &&
    imageRawResults !== null &&
    // `selectionId` carries the UUID-scoped image request ID (ticket 12).
    // A null selection can only arise from legacy callers; treat any
    // committed image payload as fresh in that case.
    (imageSelectionId === null || committedImageRequestId === imageSelectionId);
  const hasFreshSimilar =
    isSimilarMode && similarRawResults !== null && committedSimilarAssetId !== null && committedSimilarAssetId === similarModeAssetId;
  const semanticIndexedItems = useMemo(() => {
    if (!hasFreshSemantic || !composedSemantic) return [];
    return composedSemantic.items.filter((item) => item.summary.status === "indexed");
  }, [hasFreshSemantic, composedSemantic]);
  const semanticSummaries = useMemo(
    () => semanticIndexedItems.map((item) => item.summary),
    [semanticIndexedItems],
  );
  const semanticStaleCount = hasFreshSemantic && composedSemantic ? composedSemantic.stale.length : 0;
  const imageIndexedItems = useMemo(() => {
    if (!hasFreshImage || !composedImage) return [];
    return composedImage.items.filter((item) => item.summary.status === "indexed");
  }, [hasFreshImage, composedImage]);
  const imageSummaries = useMemo(
    () => imageIndexedItems.map((item) => item.summary),
    [imageIndexedItems],
  );
  const imageStaleCount = hasFreshImage && composedImage ? composedImage.stale.length : 0;
  const similarIndexedItems = useMemo(() => {
    if (!hasFreshSimilar || !composedSimilar) return [];
    return composedSimilar.items.filter((item) => item.summary.status === "indexed");
  }, [hasFreshSimilar, composedSimilar]);
  const similarSummaries = useMemo(
    () => similarIndexedItems.map((item) => item.summary),
    [similarIndexedItems],
  );
  const similarStaleCount = hasFreshSimilar && composedSimilar ? composedSimilar.stale.length : 0;

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

  if (assetsQuery.isPending) return <p aria-live="polite">Loading Assets…</p>;
  if (assetsQuery.isError) return <section className="notice notice-warning" role="alert"><strong>Could not load Assets</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void assetsQuery.refetch()}>Retry Assets</button></section>;

  const { assets, active_recipe_label: activeRecipe } = assetsQuery.data;
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
      if (isSemanticMode) {
        if (hasFreshSemantic && semanticSummaries.length) return semanticSummaries;
        if (localMatches.length) return localMatches;
        return orderedAssets;
      }
      if (isImageMode) {
        if (hasFreshImage && imageSummaries.length) return imageSummaries;
        if (localMatches.length) return localMatches;
        return orderedAssets;
      }
      if (isSimilarMode) {
        if (hasFreshSimilar && similarSummaries.length) return similarSummaries;
        if (localMatches.length) return localMatches;
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
  // Ticket 17: one selection uses the single-file client method; multiple
  // selections call the multi-file method once with stable visual-order IDs
  // (ID-only, never paths). Selection is preserved on success and failure.
  const runCopyOriginalFiles = async () => {
    const assetIds = getOrderedSelectedIds();
    if (!assetIds.length || copyPending || mutation.isPending) return;
    setCopyPending(true);
    setCopyNotice(null);
    try {
      if (assetIds.length === 1) {
        await client.copyOriginalFile(assetIds[0]);
      } else {
        await client.copyOriginalFiles(assetIds);
      }
      setCopyNotice({
        kind: "success",
        text: assetIds.length === 1 ? "Original file reference copied." : `Copied ${assetIds.length} original file references.`,
      });
    } catch (error) {
      setCopyNotice({
        kind: "error",
        text: tauriErrorDetail(error, "Copy original files failed. The Library was not modified."),
      });
    } finally {
      setCopyPending(false);
    }
  };
  const requestBatch = (action: "delete" | "rebuild-active-index") => {
    const assetIds = getOrderedSelectedIds();
    if (!assetIds.length) return;
    setConfirmation(action === "delete"
      ? { title: `Delete ${assetIds.length} selected Asset(s)?`, detail: "This deletes each Asset's Library Copy and Derived Artifacts. This cannot be undone.", confirmLabel: "Delete selected Assets", request: { kind: "batch", action, assetIds } }
      : { title: `Rebuild ${assetIds.length} selected Asset(s)?`, detail: "This clears their active-recipe embeddings and queues new indexing work. Running Asset jobs are skipped.", confirmLabel: "Queue rebuild", request: { kind: "batch", action, assetIds } });
  };

  return <>
    <section className="asset-toolbar"><p>{isFiltered ? `${orderedAssets.length} of ${assets.length} Assets` : `${assets.length} Asset${assets.length === 1 ? "" : "s"}`} · Active Index Recipe: {activeRecipe || "Not active"}</p><div className="asset-toolbar-actions"><span className="toolbar-hint">Drag image files or folders onto the asset wall to import</span></div></section>
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
    <ImportFailureDetails />
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
    {isSemanticMode ? (
      <section className="notice" role="status" aria-label="Semantic search results">
        <strong>
          {isSearching && !hasFreshSemantic
            ? `Searching the Active Index Recipe for \u201C${semanticModeQuery}\u201D\u2026`
            : `Semantic results for \u201C${semanticModeQuery}\u201D \u00B7 ${semanticSummaries.length}`}
        </strong>
        <span>Semantic results in relevance order &middot; only Indexed Assets appear here &middot; raw scores stay in advanced details.</span>
        {isSearching ? <p aria-live="polite">Searching the Active Index Recipe&hellip;</p> : null}
        {searchError ? (
          <section className="notice notice-warning" role="alert" aria-label="Semantic search error">
            <strong>Semantic search unavailable</strong>
            <span>{searchError}</span>
            <span>Library browsing remains available. Local matches for this query stay visible below.</span>
          </section>
        ) : null}
        {semanticStaleCount ? (
          <section className="notice notice-warning" role="status" aria-label="Stale semantic results">
            <strong>{semanticStaleCount} semantic result{semanticStaleCount === 1 ? "" : "s"} omitted</strong>
            <span>Their Assets are no longer in the current Asset list. Nothing was rendered with invented dimensions or Source Records.</span>
          </section>
        ) : null}
        {hasFreshSemantic && semanticIndexedItems.length ? (
          <details>
            <summary>Advanced details</summary>
            <ul className="detail-list">
              {semanticIndexedItems.map((item) => (
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
        ) : null}
        {onClearSearch ? (
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => onClearSearch()}>
              Clear search
            </button>
          </div>
        ) : null}
      </section>
    ) : null}
    {isImageMode ? (
      <section className="notice" role="status" aria-label="Image search results">
        <strong>
          {isSearching && !hasFreshImage
            ? `Searching the Active Index Recipe for the chosen image\u2026`
            : `Image results \u00B7 ${imageSummaries.length}`}
        </strong>
        <span>Image results in relevance order &middot; only Indexed Assets appear here &middot; raw scores stay in advanced details.</span>
        {isSearching ? <p aria-live="polite">Searching the Active Index Recipe&hellip;</p> : null}
        {searchError ? (
          <section className="notice notice-warning" role="alert" aria-label="Image search error">
            <strong>Image search unavailable</strong>
            <span>{searchError}</span>
            <span>Library browsing remains available. Local matches stay visible below.</span>
          </section>
        ) : null}
        {imageStaleCount ? (
          <section className="notice notice-warning" role="status" aria-label="Stale image results">
            <strong>{imageStaleCount} image result{imageStaleCount === 1 ? "" : "s"} omitted</strong>
            <span>Their Assets are no longer in the current Asset list. Nothing was rendered with invented dimensions or Source Records.</span>
          </section>
        ) : null}
        {hasFreshImage && imageIndexedItems.length ? (
          <details>
            <summary>Advanced details</summary>
            <ul className="detail-list">
              {imageIndexedItems.map((item) => (
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
        ) : null}
        {onClearSearch ? (
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => onClearSearch()}>
              Clear search
            </button>
          </div>
        ) : null}
      </section>
    ) : null}
    {isSimilarMode ? (
      <section className="notice" role="status" aria-label="Similar search results">
        <strong>
          {isSearching && !hasFreshSimilar
            ? `Finding similar Assets\u2026`
            : `Similar results \u00B7 ${similarSummaries.length}`}
        </strong>
        <span>Similar results in similarity order &middot; only Indexed Assets appear here &middot; raw scores stay in advanced details.</span>
        {isSearching ? <p aria-live="polite">Finding similar Assets in the Active Index Recipe&hellip;</p> : null}
        {searchError ? (
          <section className="notice notice-warning" role="alert" aria-label="Similar search error">
            <strong>Find Similar unavailable</strong>
            <span>{searchError}</span>
            <span>Library browsing remains available. Local matches stay visible below.</span>
          </section>
        ) : null}
        {similarStaleCount ? (
          <section className="notice notice-warning" role="status" aria-label="Stale similar results">
            <strong>{similarStaleCount} similar result{similarStaleCount === 1 ? "" : "s"} omitted</strong>
            <span>Their Assets are no longer in the current Asset list. Nothing was rendered with invented dimensions or Source Records.</span>
          </section>
        ) : null}
        {hasFreshSimilar && similarIndexedItems.length ? (
          <details>
            <summary>Advanced details</summary>
            <ul className="detail-list">
              {similarIndexedItems.map((item) => (
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
        ) : null}
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
          <AssetWaterfall
            assets={localMatches}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
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
            </div>
          </div>
        )
      ) : isSemanticMode ? (
        hasFreshSemantic && semanticSummaries.length ? (
          <AssetWaterfall
            assets={semanticSummaries}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : hasFreshSemantic && !semanticSummaries.length && !isSearching && !searchError ? (
          <div className="empty-state" aria-label="No semantic matches" ref={wallRef}>
            <h2>No semantic matches for &ldquo;{semanticModeQuery}&rdquo;</h2>
            <p>
              Only Indexed Assets appear in semantic results. Try a different description, or index
              more Assets with the Active Index Recipe.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        ) : localMatches.length ? (
          // In-progress or failed semantic request keeps the Library visible
          // through instant local matches instead of discarding browsing.
          <AssetWaterfall
            assets={localMatches}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : (
          <div className="empty-state" aria-label="No local matches" ref={wallRef}>
            <h2>No local matches for &ldquo;{semanticModeQuery}&rdquo;</h2>
            <p>
              The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
              Clearing the search restores browsing with the selected sort and filters.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        )
      ) : isImageMode ? (
        hasFreshImage && imageSummaries.length ? (
          <AssetWaterfall
            assets={imageSummaries}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : hasFreshImage && !imageSummaries.length && !isSearching && !searchError ? (
          <div className="empty-state" aria-label="No image matches" ref={wallRef}>
            <h2>No image matches</h2>
            <p>
              Only Indexed Assets appear in image results. Try another image, or index
              more Assets with the Active Index Recipe.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        ) : localMatches.length ? (
          // In-progress or failed image request keeps the Library visible
          // through local matches instead of discarding browsing.
          <AssetWaterfall
            assets={localMatches}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : (
          <div className="empty-state" aria-label="No local matches" ref={wallRef}>
            <h2>No local matches</h2>
            <p>
              The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
              Clearing the search restores browsing with the selected sort and filters.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        )
      ) : isSimilarMode ? (
        hasFreshSimilar && similarSummaries.length ? (
          <AssetWaterfall
            assets={similarSummaries}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : hasFreshSimilar && !similarSummaries.length && !isSearching && !searchError ? (
          <div className="empty-state" aria-label="No similar matches" ref={wallRef}>
            <h2>No similar Assets</h2>
            <p>
              Only Indexed Assets appear in similar results. Index more Assets with
              the Active Index Recipe to expand this search.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        ) : localMatches.length ? (
          // In-progress or failed similar request keeps the Library visible
          // through local matches instead of discarding browsing.
          <AssetWaterfall
            assets={localMatches}
            density={density}
            checkedIds={selectedIds}
            onOpenAsset={onSelectAsset}
            onToggleChecked={toggleAsset}
            onFindSimilar={onFindSimilar}
            sectionRef={wallRef}
            accepting={Boolean(dragPreview)}
          />
        ) : (
          <div className="empty-state" aria-label="No local matches" ref={wallRef}>
            <h2>No local matches</h2>
            <p>
              The Library still holds {assets.length} Asset{assets.length === 1 ? "" : "s"}.
              Clearing the search restores browsing with the selected sort and filters.
            </p>
            <div>
              <button className="button button-secondary" type="button" onClick={() => onClearSearch?.()}>
                Clear search
              </button>
            </div>
          </div>
        )
      ) : orderedAssets.length ? (
        <AssetWaterfall
          assets={orderedAssets}
          density={density}
          checkedIds={selectedIds}
          onOpenAsset={onSelectAsset}
          onToggleChecked={toggleAsset}
          onFindSimilar={onFindSimilar}
          sectionRef={wallRef}
          accepting={Boolean(dragPreview)}
        />
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
    {confirmation ? <ConfirmDialog action={confirmation} pending={mutation.isPending} onCancel={() => setConfirmation(null)} onConfirm={() => mutation.mutate(confirmation.request)} /> : null}
  </>;
}
