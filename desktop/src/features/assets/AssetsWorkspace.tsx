import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { mediaUrl } from "../../api/media-url";
import { tauriErrorDetail } from "../../api/tauri-error";
import {
  subscribeNativeDrag,
  type NativeDragSubscribe,
} from "../../api/native-drag";
import type { AssetDetail, AssetSummary } from "../../api/types";

interface AssetsWorkspaceProps {
  client: MemeSortClient;
  selectedAssetId: string | null;
  onSelectAsset: (assetId: string) => void;
  onCloseDetail: () => void;
  nativeDrag?: NativeDragSubscribe;
}

type MutationRequest =
  | { kind: "delete-asset"; assetId: string }
  | { kind: "remove-source"; assetId: string; sourcePath: string }
  | { kind: "batch"; action: "delete" | "rebuild-active-index"; assetIds: string[] };

type RevealRequest = { assetId: string; target: "managed" | "source"; sourcePath?: string };

type LibraryNotice = { kind: "error" | "success"; text: string };

interface ConfirmAction {
  title: string;
  detail: string;
  confirmLabel: string;
  request: MutationRequest;
}

function assetName(asset: AssetSummary): string {
  const sourceName = asset.source_records[0]?.source_path.split(/[\\/]/).pop();
  return sourceName || asset.library_path.split("/").pop() || asset.asset_id;
}

function dimensions(asset: AssetSummary): string {
  return asset.width && asset.height ? `${asset.width} × ${asset.height}` : "Dimensions unavailable";
}

function statusLabel(status: AssetSummary["status"]): string {
  return `${status.charAt(0).toUpperCase()}${status.slice(1)} Asset`;
}

function AssetPreview({ asset, className }: { asset: AssetSummary; className: string }) {
  const preview = mediaUrl(asset.thumbnail_url) ?? mediaUrl(asset.library_url);
  if (!preview) return <div className={`${className} media-placeholder`} aria-label="Preview unavailable" />;
  return <img className={className} src={preview} alt={`${assetName(asset)} preview`} loading="lazy" />;
}

function AssetCard({ asset, checked, onSelect, onToggle }: { asset: AssetSummary; checked: boolean; onSelect: () => void; onToggle: () => void }) {
  const name = assetName(asset);
  return (
    <article className="asset-card">
      <button className="asset-card-open" type="button" onClick={onSelect} aria-label={`Open ${name}`}>
        <AssetPreview asset={asset} className="asset-card-media" />
        <span className="asset-card-body">
          <span className="asset-title">{name}</span>
          <span className="asset-subtitle">{dimensions(asset)} · {asset.media_type}</span>
          <span className={`status-pill status-${asset.status}`}>{statusLabel(asset.status)}</span>
        </span>
      </button>
      <label className="asset-select"><input type="checkbox" checked={checked} onChange={onToggle} /> Select {name}</label>
    </article>
  );
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

function AssetDetailDialog({ assetId, client, onClose, onDeleteAsset, onRevealManaged, onRemoveSourceRecord, onRevealSource, mutating, revealing }: { assetId: string; client: MemeSortClient; onClose: () => void; onDeleteAsset: () => void; onRevealManaged: () => void; onRemoveSourceRecord: (sourcePath: string) => void; onRevealSource: (sourcePath: string) => void; mutating: boolean; revealing: boolean }) {
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

export function AssetsWorkspace({ client, selectedAssetId, onSelectAsset, onCloseDetail, nativeDrag }: AssetsWorkspaceProps) {
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [confirmation, setConfirmation] = useState<ConfirmAction | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [libraryNotice, setLibraryNotice] = useState<LibraryNotice | null>(null);
  const [libraryBusy, setLibraryBusy] = useState<"files" | "folder" | null>(null);
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
      setSelectedIds(new Set());
      setConfirmation(null);
      onCloseDetail();
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["assets"] }), queryClient.invalidateQueries({ queryKey: ["app-state"] })]);
    },
    onError: () => { setFeedback("The requested Asset change could not be completed. The Library was not modified by the desktop UI."); setConfirmation(null); },
  });
  const revealMutation = useMutation({
    mutationFn: async (request: RevealRequest) => request.sourcePath === undefined
      ? client.revealAsset(request.assetId, request.target)
      : client.revealAsset(request.assetId, request.target, request.sourcePath),
    onSuccess: (_result, request) => { setFeedback(request.target === "managed" ? "Opened the managed Library Copy in File Explorer." : "Opened the recorded Source Path in File Explorer."); },
    onError: () => { setFeedback("The requested file could not be opened in File Explorer. The Library was not modified."); },
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
        await client.startLibraryImport(dropId);
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
  }, [client, nativeDragSubscribe, queryClient]);

  if (assetsQuery.isPending) return <p aria-live="polite">Loading Assets…</p>;
  if (assetsQuery.isError) return <section className="notice notice-warning" role="alert"><strong>Could not load Assets</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void assetsQuery.refetch()}>Retry Assets</button></section>;

  const { assets, active_recipe_label: activeRecipe } = assetsQuery.data;
  const toggleAsset = (assetId: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(assetId)) next.delete(assetId);
    else next.add(assetId);
    return next;
  });
  const requestBatch = (action: "delete" | "rebuild-active-index") => {
    const assetIds = [...selectedIds];
    if (!assetIds.length) return;
    setConfirmation(action === "delete"
      ? { title: `Delete ${assetIds.length} selected Asset(s)?`, detail: "This deletes each Asset's Library Copy and Derived Artifacts. This cannot be undone.", confirmLabel: "Delete selected Assets", request: { kind: "batch", action, assetIds } }
      : { title: `Rebuild ${assetIds.length} selected Asset(s)?`, detail: "This clears their active-recipe embeddings and queues new indexing work. Running Asset jobs are skipped.", confirmLabel: "Queue rebuild", request: { kind: "batch", action, assetIds } });
  };
  const requestDeleteAsset = (assetId: string) => setConfirmation({ title: "Delete this Asset?", detail: "This deletes its Library Copy, Source Records, and Derived Artifacts. This cannot be undone.", confirmLabel: "Delete Asset", request: { kind: "delete-asset", assetId } });
  const requestRemoveSource = (assetId: string, sourcePath: string) => setConfirmation({ title: "Remove this Source Record?", detail: "If this is the final Source Record, MemeSort deletes the resulting Orphan Asset and its Derived Artifacts.", confirmLabel: "Remove Source Record", request: { kind: "remove-source", assetId, sourcePath } });
  const startLibrarySelection = async (kind: "files" | "folder") => {
    setLibraryBusy(kind);
    setLibraryNotice(null);
    try {
      const selection = kind === "files" ? await client.chooseLibraryFiles() : await client.chooseLibraryFolder();
      if (!selection) return;
      const label = kind === "files" ? "file" : "folder";
      const count = selection.count;
      setLibraryNotice({ kind: "success", text: `Starting Import Batch for ${count} ${label}${count === 1 ? "" : "s"}.` });
      await client.startLibraryImport(selection.selection_id);
      setLibraryNotice({ kind: "success", text: `Import Batch started for ${count} ${label}${count === 1 ? "" : "s"}.` });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["app-state"] })]);
    } catch (error) {
      setLibraryNotice({ kind: "error", text: tauriErrorDetail(error, "MemeSort could not start the Library Import Batch. Make a fresh native selection to retry.") });
    } finally {
      setLibraryBusy(null);
    }
  };

  return <>
    <section className="asset-toolbar"><p>{assets.length} Asset{assets.length === 1 ? "" : "s"} · Active Index Recipe: {activeRecipe || "Not active"}</p><div className="asset-toolbar-actions"><span className="toolbar-hint">Drag image files or folders onto the asset wall to import</span><button className="button button-secondary" type="button" disabled={libraryBusy !== null} onClick={() => void startLibrarySelection("files")}>Choose files</button><button className="button button-secondary" type="button" disabled={libraryBusy !== null} onClick={() => void startLibrarySelection("folder")}>Choose folder</button><span>{selectedIds.size} selected</span><button className="button button-secondary" type="button" disabled={!selectedIds.size || mutation.isPending} onClick={() => requestBatch("rebuild-active-index")}>Rebuild Active Index</button><button className="button button-danger" type="button" disabled={!selectedIds.size || mutation.isPending} onClick={() => requestBatch("delete")}>Delete selected</button></div></section>
    {feedback ? <section className="notice notice-success" role="status"><span>{feedback}</span></section> : null}
    {libraryNotice ? <section className={`notice ${libraryNotice.kind === "error" ? "notice-warning" : "notice-success"}`} role={libraryNotice.kind === "error" ? "alert" : "status"}><span>{libraryNotice.text}</span></section> : null}
    <div className="asset-wall">
      {assets.length ? (
        <section className={`asset-grid${dragPreview ? " asset-grid-accepting" : ""}`} aria-label="Assets" ref={wallRef}>{assets.map((asset) => <AssetCard key={asset.asset_id} asset={asset} checked={selectedIds.has(asset.asset_id)} onSelect={() => onSelectAsset(asset.asset_id)} onToggle={() => toggleAsset(asset.asset_id)} />)}</section>
      ) : (
        <div className={`import-drop-card${dragPreview ? " import-drop-card-accepting" : ""}`} aria-label="Import drop card" ref={wallRef}>
          <h2>Drag image files or folders here</h2>
          <p>Release them over this card to start an Import Batch. Sources are scanned and validated before anything is written to the Library.</p>
          <p className="import-drop-card-hint">Keyboard alternative: use Choose files or Choose folder in the toolbar.</p>
        </div>
      )}
      {dragPreview ? (
        <div className="drop-cue" role="status" aria-live="polite" aria-label="Release to import">
          <strong>Release to import</strong>
          <span>{dragPreview.itemCount} item{dragPreview.itemCount === 1 ? "" : "s"} ready</span>
        </div>
      ) : null}
    </div>
    {selectedAssetId ? <AssetDetailDialog assetId={selectedAssetId} client={client} onClose={onCloseDetail} onDeleteAsset={() => requestDeleteAsset(selectedAssetId)} onRevealManaged={() => revealMutation.mutate({ assetId: selectedAssetId, target: "managed" })} onRemoveSourceRecord={(sourcePath) => requestRemoveSource(selectedAssetId, sourcePath)} onRevealSource={(sourcePath) => revealMutation.mutate({ assetId: selectedAssetId, target: "source", sourcePath })} mutating={mutation.isPending} revealing={revealMutation.isPending} /> : null}
    {confirmation ? <ConfirmDialog action={confirmation} pending={mutation.isPending} onCancel={() => setConfirmation(null)} onConfirm={() => mutation.mutate(confirmation.request)} /> : null}
  </>;
}
