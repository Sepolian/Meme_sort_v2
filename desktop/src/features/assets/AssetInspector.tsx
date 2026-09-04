import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import { mediaUrl } from "../../api/media-url";
import { getAssetDisplayName } from "../library/libraryOrdering";
import { AssetContextMenu } from "./AssetContextMenu";
import { useAssetContextMenu } from "./useAssetContextMenu";
import type { AssetDetail, AssetListResult, AssetSummary } from "../../api/types";

export interface AssetInspectorProps {
  assetId: string;
  client: MemeSortClient;
  onClose: () => void;
  /** Called after a successful delete so the parent can reconcile selection. */
  onDeleted?: (assetId: string) => void;
  /**
   * Find Similar action point required by ticket 12.
   * Ticket 10 only exposes the entry point with the Asset ID; ticket 12 owns
   * result composition and transient result mode.
   */
  onFindSimilar?: (assetId: string) => void;
}

type CopyStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

function assetName(asset: AssetSummary): string {
  return getAssetDisplayName(asset);
}

function dimensionsLabel(asset: AssetSummary): string {
  return asset.width && asset.height
    ? `${asset.width} × ${asset.height}`
    : "Dimensions unavailable";
}

function statusLabel(status: AssetSummary["status"]): string {
  return `${status.charAt(0).toUpperCase()}${status.slice(1)} Asset`;
}

function InspectorSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="inspector-section" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

/**
 * Right-side non-overlay inspector (ticket 10).
 *
 * - Opened from `asset=<asset-id>`; closing removes only that param via
 *   `onClose` (ticket 07 contract). The waterfall stays mounted because this
 *   renders inside `LibraryShell`'s `aside`, never as a dialog overlay.
 * - Primary area: large preview, Clipboard Copy, Find Similar, Reveal.
 * - Secondary: dimensions/media/import, OCR, Source Records.
 * - Collapsed advanced: Active Index Recipe + Jobs.
 * - Overflow: Copy original file + confirmed Delete.
 * - Only Asset IDs cross the client seam for clipboard/delete/reveal-managed;
 *   recorded Source Paths come from server data for reveal-source/remove-source.
 * - Copy failure never claims clipboard rollback; it keeps browsing usable and
 *   offers Reveal in Explorer.
 */
export function AssetInspector({
  assetId,
  client,
  onClose,
  onDeleted,
  onFindSimilar,
}: AssetInspectorProps) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ["asset-detail", assetId],
    queryFn: () => client.getAssetDetail(assetId),
  });

  const [copyState, setCopyState] = useState<CopyStatus>({ kind: "idle" });
  const [copyOriginalState, setCopyOriginalState] = useState<CopyStatus>({
    kind: "idle",
  });
  const [revealState, setRevealState] = useState<CopyStatus>({ kind: "idle" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirmRemoveSource, setConfirmRemoveSource] = useState<string | null>(
    null,
  );
  const [isRemovingSource, setIsRemovingSource] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  // Reset transient action state when the inspected Asset changes.
  useEffect(() => {
    setCopyState({ kind: "idle" });
    setCopyOriginalState({ kind: "idle" });
    setRevealState({ kind: "idle" });
    setConfirmDelete(false);
    setIsDeleting(false);
    setDeleteError(null);
    setConfirmRemoveSource(null);
    setIsRemovingSource(false);
    setActionFeedback(null);
  }, [assetId]);

  // Escape closes the inspector without touching other URL params (the
  // caller implements `clearAssetId` from ticket 07).
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !confirmDelete && !confirmRemoveSource) {
        onClose();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, confirmDelete, confirmRemoveSource]);

  const runClipboardCopy = async () => {
    setCopyState({ kind: "pending" });
    try {
      // ID-only: Rust resolves the managed Library Copy; no WebView paths.
      await client.copyAssetToClipboard(assetId);
      // Success keeps the inspector open and preserves selection (no onClose,
      // no selection mutation here).
      setCopyState({
        kind: "success",
        message: "Copied to clipboard. Paste into QQ or WeChat.",
      });
    } catch (error) {
      setCopyState({
        kind: "error",
        message: tauriErrorDetail(
          error,
          "Clipboard Copy failed. The Library was not modified. Use Reveal in Explorer to locate the file.",
        ),
      });
    }
  };

  const runCopyOriginal = async () => {
    setCopyOriginalState({ kind: "pending" });
    try {
      // Raw Library Copy reference command, ID-only.
      await client.copyOriginalFile(assetId);
      setCopyOriginalState({
        kind: "success",
        message: "Original file reference copied.",
      });
    } catch (error) {
      setCopyOriginalState({
        kind: "error",
        message: tauriErrorDetail(
          error,
          "Copy original file failed. The Library was not modified.",
        ),
      });
    }
  };

  const runRevealManaged = async () => {
    setRevealState({ kind: "pending" });
    try {
      await client.revealAsset(assetId, "managed");
      setRevealState({
        kind: "success",
        message: "Opened the managed Library Copy in File Explorer.",
      });
    } catch (error) {
      setRevealState({
        kind: "error",
        message: tauriErrorDetail(
          error,
          "The requested file could not be opened in File Explorer. The Library was not modified.",
        ),
      });
    }
  };

  const runRevealSource = async (sourcePath: string) => {
    setActionFeedback(null);
    try {
      await client.revealAsset(assetId, "source", sourcePath);
      setActionFeedback("Opened the recorded Source Path in File Explorer.");
    } catch {
      setActionFeedback(
        "The requested file could not be opened in File Explorer. The Library was not modified.",
      );
    }
  };

  const runRemoveSource = async (sourcePath: string) => {
    setIsRemovingSource(true);
    try {
      const result = await client.removeSourceRecord(assetId, sourcePath);
      setConfirmRemoveSource(null);
      setActionFeedback(
        result.asset_deleted
          ? "Removed the final Source Record and deleted the Orphan Asset."
          : "Removed the Source Record.",
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["app-state"] }),
        queryClient.invalidateQueries({ queryKey: ["asset-detail", assetId] }),
      ]);
      if (result.asset_deleted) {
        queryClient.setQueryData<AssetListResult | undefined>(
          ["assets"],
          (old) =>
            old
              ? {
                  ...old,
                  assets: old.assets.filter(
                    (item) => item.asset_id !== assetId,
                  ),
                }
              : old,
        );
        onDeleted?.(assetId);
        onClose();
      }
    } catch (error) {
      setActionFeedback(
        tauriErrorDetail(
          error,
          "The requested Asset change could not be completed. The Library was not modified by the desktop UI.",
        ),
      );
      setConfirmRemoveSource(null);
    } finally {
      setIsRemovingSource(false);
    }
  };

  const runConfirmedDelete = async () => {
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await client.deleteAsset(assetId);
      // Remove from the visible wall immediately so tests and users see the
      // deletion even before the refetch settles; invalidation then syncs
      // counts and server state.
      queryClient.setQueryData<AssetListResult | undefined>(
        ["assets"],
        (old) =>
          old
            ? {
                ...old,
                assets: old.assets.filter((item) => item.asset_id !== assetId),
              }
            : old,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["app-state"] }),
      ]);
      queryClient.removeQueries({ queryKey: ["asset-detail", assetId] });
      setConfirmDelete(false);
      onDeleted?.(assetId);
      // Close only this inspector (caller removes only `asset`).
      onClose();
    } catch (error) {
      setDeleteError(
        tauriErrorDetail(
          error,
          "The requested Asset change could not be completed. The Library was not modified by the desktop UI.",
        ),
      );
      setConfirmDelete(false);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleFindSimilar = () => {
    // Action point for ticket 12: always passes the Asset ID, never paths.
    // When the parent wires Library search (ticket 12) it receives the ID;
    // until then the button remains discoverable without inventing results.
    if (onFindSimilar) {
      onFindSimilar(assetId);
    } else {
      setActionFeedback(
        "Find Similar will search from this Asset once Library search lands.",
      );
    }
  };

  return (
    <section
      className="inspector"
      aria-label="Asset inspector"
      data-asset-id={assetId}
    >
      <div className="inspector-header">
        <div>
          <p className="eyebrow">Inspector</p>
          <h2>Asset inspector</h2>
        </div>
        <button
          className="button button-secondary"
          type="button"
          onClick={onClose}
        >
          Close inspector
        </button>
      </div>

      {detailQuery.isPending ? (
        <p aria-live="polite">Loading Asset detail…</p>
      ) : null}
      {detailQuery.isError ? (
        <section className="notice notice-warning" role="alert">
          <strong>Asset details are unavailable</strong>
          <span>
            {tauriErrorDetail(
              detailQuery.error,
              "This Asset may no longer exist in the Library. Refresh the Asset wall and try again.",
            )}
          </span>
          <div className="import-actions">
            <button
              className="button button-secondary"
              type="button"
              onClick={() => void detailQuery.refetch()}
            >
              Retry Asset detail
            </button>
          </div>
        </section>
      ) : null}

      {detailQuery.data ? (
        <InspectorBody
          asset={detailQuery.data.asset}
          activeRecipeLabel={detailQuery.data.active_recipe_label}
          copyState={copyState}
          copyOriginalState={copyOriginalState}
          revealState={revealState}
          actionFeedback={actionFeedback}
          deleteError={deleteError}
          isDeleting={isDeleting}
          confirmDelete={confirmDelete}
          confirmRemoveSource={confirmRemoveSource}
          isRemovingSource={isRemovingSource}
          onCopy={runClipboardCopy}
          onCopyOriginal={runCopyOriginal}
          onRevealManaged={runRevealManaged}
          onRevealSource={runRevealSource}
          onFindSimilar={handleFindSimilar}
          onRequestDelete={() => {
            setDeleteError(null);
            setConfirmDelete(true);
          }}
          onCancelDelete={() => setConfirmDelete(false)}
          onConfirmDelete={runConfirmedDelete}
          onRequestRemoveSource={setConfirmRemoveSource}
          onCancelRemoveSource={() => setConfirmRemoveSource(null)}
          onConfirmRemoveSource={runRemoveSource}
        />
      ) : null}
    </section>
  );
}

function InspectorBody({
  asset,
  activeRecipeLabel,
  copyState,
  copyOriginalState,
  revealState,
  actionFeedback,
  deleteError,
  isDeleting,
  confirmDelete,
  confirmRemoveSource,
  isRemovingSource,
  onCopy,
  onCopyOriginal,
  onRevealManaged,
  onRevealSource,
  onFindSimilar,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  onRequestRemoveSource,
  onCancelRemoveSource,
  onConfirmRemoveSource,
}: {
  asset: AssetDetail;
  activeRecipeLabel: string;
  copyState: CopyStatus;
  copyOriginalState: CopyStatus;
  revealState: CopyStatus;
  actionFeedback: string | null;
  deleteError: string | null;
  isDeleting: boolean;
  confirmDelete: boolean;
  confirmRemoveSource: string | null;
  isRemovingSource: boolean;
  onCopy: () => void;
  onCopyOriginal: () => void;
  onRevealManaged: () => void;
  onRevealSource: (sourcePath: string) => void;
  onFindSimilar: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  onRequestRemoveSource: (sourcePath: string) => void;
  onCancelRemoveSource: () => void;
  onConfirmRemoveSource: (sourcePath: string) => void;
}) {
  const name = assetName(asset);
  const preview = mediaUrl(asset.library_url);
  const previewMenu = useAssetContextMenu();
  const copyPending = copyState.kind === "pending";
  const copyOriginalPending = copyOriginalState.kind === "pending";
  const revealing = revealState.kind === "pending";
  // Collapsed by default (ticket 10); React state (not native <details>)
  // so jsdom tests can toggle reliably via the buttons below.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);

  return (
    <div className="inspector-body">
      {/* Primary area: large preview + Clipboard Copy + Find Similar + Reveal */}
      <section className="inspector-primary" aria-label="Primary actions">
        {preview ? (
          <img
            className="inspector-preview"
            src={preview}
            alt={`${name} preview`}
            // Same trap as the wall cards: the WebView-native image menu
            // copies the rendered static bitmap, so right-click offers the
            // native commands through the app menu instead.
            onContextMenu={previewMenu.openMenu}
          />
        ) : (
          <div
            className="inspector-preview media-placeholder"
            aria-label={`${name} preview unavailable`}
          />
        )}
        {previewMenu.anchor ? (
          <AssetContextMenu
            x={previewMenu.anchor.x}
            y={previewMenu.anchor.y}
            menuLabel={`Actions for ${name}`}
            items={[
              { label: "Copy image", onSelect: onCopy },
              { label: "Copy original file", onSelect: onCopyOriginal },
            ]}
            onClose={previewMenu.closeMenu}
          />
        ) : null}
        <div>
          <span className={`status-pill status-${asset.status}`}>
            {statusLabel(asset.status)}
          </span>
          <h3>{name}</h3>
          <p>
            {dimensionsLabel(asset)} · {asset.media_type} ·{" "}
            {asset.source_record_count} Source Record
            {asset.source_record_count === 1 ? "" : "s"}
          </p>
        </div>
        <div className="inspector-primary-actions">
          <button
            className="button"
            type="button"
            disabled={copyPending}
            onClick={onCopy}
          >
            {copyPending ? "Copying…" : "Copy to Clipboard"}
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={onFindSimilar}
          >
            Find Similar
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={revealing}
            onClick={onRevealManaged}
          >
            {revealing ? "Revealing…" : "Reveal in Explorer"}
          </button>
        </div>
        {copyState.kind === "success" ? (
          <section className="notice notice-success" role="status">
            <span>{copyState.message}</span>
          </section>
        ) : null}
        {copyState.kind === "error" ? (
          <section
            className="notice notice-warning"
            role="alert"
            aria-label="Clipboard Copy failed"
          >
            <strong>Clipboard Copy failed</strong>
            <span>{copyState.message}</span>
            <span>
              The Library was not modified. Existing clipboard content was left
              as-is where possible.
            </span>
            <div className="import-actions">
              <button
                className="button button-secondary"
                type="button"
                disabled={revealing}
                onClick={onRevealManaged}
              >
                Reveal in Explorer
              </button>
            </div>
          </section>
        ) : null}
        {revealState.kind === "success" ? (
          <section className="notice notice-success" role="status">
            <span>{revealState.message}</span>
          </section>
        ) : null}
        {revealState.kind === "error" ? (
          <section className="notice notice-warning" role="alert">
            <strong>Reveal failed</strong>
            <span>{revealState.message}</span>
          </section>
        ) : null}
      </section>

      {/* Secondary area: dimensions/media/import, OCR, Source Records */}
      <InspectorSection title="Details">
        <p>
          {dimensionsLabel(asset)} · {asset.media_type}
        </p>
        <p>Library path: {asset.library_path}</p>
        <p>Imported: {asset.imported_at}</p>
        <p>Updated: {asset.updated_at}</p>
        <p>
          {asset.source_record_count} Source Record
          {asset.source_record_count === 1 ? "" : "s"}
        </p>
      </InspectorSection>

      <InspectorSection title="OCR">
        {asset.ocr_results.length ? (
          <ul className="detail-list">
            {asset.ocr_results.map((result) => (
              <li key={result.result_id}>
                {result.text || "OCR result contains no text."}
              </li>
            ))}
          </ul>
        ) : (
          <p>No OCR text is available for this Asset.</p>
        )}
      </InspectorSection>

      <InspectorSection title="Source Records">
        {asset.source_records.length ? (
          <ul className="detail-list">
            {asset.source_records.map((source) => (
              <li key={source.source_path}>
                <span className="mono">{source.source_path}</span>
                <button
                  className="text-button detail-action"
                  type="button"
                  onClick={() => onRevealSource(source.source_path)}
                >
                  Reveal Source
                </button>
                <button
                  className="text-button detail-action"
                  type="button"
                  onClick={() => onRequestRemoveSource(source.source_path)}
                >
                  Remove Source Record
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>No Source Records are available.</p>
        )}
      </InspectorSection>

      {/* Collapsed advanced area: Active Index Recipe + Jobs */}
      <section className="inspector-advanced" aria-label="Advanced">
        <button
          className="button button-secondary"
          type="button"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((open) => !open)}
        >
          {advancedOpen ? "Hide Advanced" : "Advanced"}
        </button>
        {advancedOpen ? (
          <>
            <InspectorSection title="Active Index Recipe">
              <p>Active recipe: {activeRecipeLabel || "Not active"}</p>
              <p>
                Indexed recipes: {asset.indexed_recipe_labels.join(", ") || "None yet"}
              </p>
              {asset.stale_recipe_labels.length ? (
                <p>Stale recipes: {asset.stale_recipe_labels.join(", ")}</p>
              ) : null}
            </InspectorSection>
            <InspectorSection title="Jobs">
              {asset.jobs.length ? (
                <ul className="detail-list">
                  {asset.jobs.map((job) => (
                    <li key={job.job_id}>
                      {job.type} · {job.status} · attempt {job.attempt_count}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No jobs are recorded for this Asset.</p>
              )}
            </InspectorSection>
          </>
        ) : null}
      </section>

      {/* Overflow: Copy original file + confirmed Delete */}
      <section className="inspector-overflow" aria-label="More actions">
        <button
          className="button button-secondary"
          type="button"
          aria-expanded={overflowOpen}
          onClick={() => setOverflowOpen((open) => !open)}
        >
          {overflowOpen ? "Hide More actions" : "More actions"}
        </button>
        {overflowOpen ? (
          <>
            <div className="inspector-overflow-actions">
              <button
                className="button button-secondary"
                type="button"
                disabled={copyOriginalPending}
                onClick={onCopyOriginal}
              >
                {copyOriginalPending ? "Copying…" : "Copy original file"}
              </button>
              <button
                className="button button-danger"
                type="button"
                disabled={isDeleting}
                onClick={onRequestDelete}
              >
                Delete Asset
              </button>
            </div>
            {copyOriginalState.kind === "success" ? (
              <section className="notice notice-success" role="status">
                <span>{copyOriginalState.message}</span>
              </section>
            ) : null}
            {copyOriginalState.kind === "error" ? (
              <section className="notice notice-warning" role="alert">
                <strong>Copy original file failed</strong>
                <span>{copyOriginalState.message}</span>
              </section>
            ) : null}
          </>
        ) : null}
      </section>

      {actionFeedback ? (
        <section className="notice notice-success" role="status">
          <span>{actionFeedback}</span>
        </section>
      ) : null}
      {deleteError ? (
        <section className="notice notice-warning" role="alert">
          <strong>Delete failed</strong>
          <span>{deleteError}</span>
        </section>
      ) : null}

      {confirmDelete ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={isDeleting ? undefined : onCancelDelete}
        >
          <section
            className="dialog confirm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="inspector-delete-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="eyebrow">Confirm change</p>
            <h2 id="inspector-delete-title">Delete this Asset?</h2>
            <p>
              This deletes its Library Copy, Source Records, and Derived
              Artifacts. This cannot be undone.
            </p>
            <div className="dialog-actions">
              <button
                className="button button-secondary"
                type="button"
                disabled={isDeleting}
                onClick={onCancelDelete}
              >
                Cancel
              </button>
              <button
                className="button button-danger"
                type="button"
                autoFocus
                disabled={isDeleting}
                onClick={onConfirmDelete}
              >
                {isDeleting ? "Working…" : "Delete Asset"}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {confirmRemoveSource ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={isRemovingSource ? undefined : onCancelRemoveSource}
        >
          <section
            className="dialog confirm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="inspector-remove-source-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <p className="eyebrow">Confirm change</p>
            <h2 id="inspector-remove-source-title">
              Remove this Source Record?
            </h2>
            <p>
              If this is the final Source Record, MemeSort deletes the resulting
              Orphan Asset and its Derived Artifacts.
            </p>
            <div className="dialog-actions">
              <button
                className="button button-secondary"
                type="button"
                disabled={isRemovingSource}
                onClick={onCancelRemoveSource}
              >
                Cancel
              </button>
              <button
                className="button button-danger"
                type="button"
                autoFocus
                disabled={isRemovingSource}
                onClick={() => onConfirmRemoveSource(confirmRemoveSource)}
              >
                {isRemovingSource ? "Working…" : "Remove Source Record"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

export default AssetInspector;
