import { useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { mediaUrl } from "../../api/media-url";
import type { AssetDetail, AssetSummary } from "../../api/types";
import { EmptyState } from "../../components/States";

interface AssetsWorkspaceProps {
  client: MemeSortClient;
  selectedAssetId: string | null;
  onSelectAsset: (assetId: string) => void;
  onCloseDetail: () => void;
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
  if (!preview) {
    return <div className={`${className} media-placeholder`} aria-label="Preview unavailable" />;
  }
  return <img className={className} src={preview} alt={`${assetName(asset)} preview`} loading="lazy" />;
}

function AssetCard({ asset, onSelect }: { asset: AssetSummary; onSelect: () => void }) {
  return (
    <button className="asset-card" type="button" onClick={onSelect}>
      <AssetPreview asset={asset} className="asset-card-media" />
      <span className="asset-card-body">
        <span className="asset-title">{assetName(asset)}</span>
        <span className="asset-subtitle">{dimensions(asset)} · {asset.media_type}</span>
        <span className={`status-pill status-${asset.status}`}>{statusLabel(asset.status)}</span>
      </span>
    </button>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function AssetDetailContent({ asset }: { asset: AssetDetail }) {
  const preview = mediaUrl(asset.library_url);
  return (
    <div className="detail-content">
      <div className="detail-hero">
        {preview ? <img className="detail-media" src={preview} alt={`${assetName(asset)} preview`} /> : <div className="detail-media media-placeholder" />}
        <div>
          <span className={`status-pill status-${asset.status}`}>{statusLabel(asset.status)}</span>
          <h3>{assetName(asset)}</h3>
          <p>{dimensions(asset)} · {asset.media_type} · {asset.source_record_count} Source Record{asset.source_record_count === 1 ? "" : "s"}</p>
        </div>
      </div>
      <div className="detail-sections">
        <DetailSection title="Index recipes">
          <p>Active recipes: {asset.indexed_recipe_labels.join(", ") || "None yet"}</p>
          {asset.stale_recipe_labels.length ? <p>Stale recipes: {asset.stale_recipe_labels.join(", ")}</p> : null}
        </DetailSection>
        <DetailSection title="Source Records">
          {asset.source_records.length ? (
            <ul className="detail-list">
              {asset.source_records.map((source) => <li key={source.source_path}><span className="mono">{source.source_path}</span></li>)}
            </ul>
          ) : <p>No Source Records are available.</p>}
        </DetailSection>
        <DetailSection title="OCR">
          {asset.ocr_results.length ? (
            <ul className="detail-list">
              {asset.ocr_results.map((result) => <li key={result.result_id}>{result.text || "OCR result contains no text."}</li>)}
            </ul>
          ) : <p>No OCR text is available for this Asset.</p>}
        </DetailSection>
        <DetailSection title="Jobs">
          {asset.jobs.length ? (
            <ul className="detail-list">
              {asset.jobs.map((job) => <li key={job.job_id}>{job.type} · {job.status} · attempt {job.attempt_count}</li>)}
            </ul>
          ) : <p>No jobs are recorded for this Asset.</p>}
        </DetailSection>
      </div>
    </div>
  );
}

function AssetDetailDialog({ assetId, client, onClose }: { assetId: string; client: MemeSortClient; onClose: () => void }) {
  const detailQuery = useQuery({
    queryKey: ["asset-detail", assetId],
    queryFn: () => client.getAssetDetail(assetId),
  });

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="dialog detail-dialog" role="dialog" aria-modal="true" aria-labelledby="asset-detail-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-title-row">
          <div>
            <p className="eyebrow">Asset detail</p>
            <h2 id="asset-detail-title">Asset details</h2>
          </div>
          <button className="button button-secondary" type="button" autoFocus onClick={onClose}>Close</button>
        </div>
        {detailQuery.isPending ? <p aria-live="polite">Loading Asset detail…</p> : null}
        {detailQuery.isError ? <section className="notice notice-warning" role="alert"><strong>Asset details are unavailable</strong><span>This Asset may no longer exist in the Library. Refresh the Asset wall and try again.</span></section> : null}
        {detailQuery.data ? <AssetDetailContent asset={detailQuery.data.asset} /> : null}
      </section>
    </div>
  );
}

export function AssetsWorkspace({ client, selectedAssetId, onSelectAsset, onCloseDetail }: AssetsWorkspaceProps) {
  const assetsQuery = useQuery({
    queryKey: ["assets"],
    queryFn: () => client.getAssets(),
  });

  if (assetsQuery.isPending) return <p aria-live="polite">Loading Assets…</p>;
  if (assetsQuery.isError) {
    return <section className="notice notice-warning" role="alert"><strong>Could not load Assets</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void assetsQuery.refetch()}>Retry Assets</button></section>;
  }

  const { assets, active_recipe_label: activeRecipe } = assetsQuery.data;
  return (
    <>
      <section className="asset-toolbar">
        <p>{assets.length} Asset{assets.length === 1 ? "" : "s"} · Active Index Recipe: {activeRecipe || "Not active"}</p>
      </section>
      {assets.length ? (
        <section className="asset-grid" aria-label="Assets">
          {assets.map((asset) => <AssetCard key={asset.asset_id} asset={asset} onSelect={() => onSelectAsset(asset.asset_id)} />)}
        </section>
      ) : <EmptyState title="No Assets yet" detail="Import a folder from Setup to create managed Library Copies." />}
      {selectedAssetId ? <AssetDetailDialog assetId={selectedAssetId} client={client} onClose={onCloseDetail} /> : null}
    </>
  );
}
