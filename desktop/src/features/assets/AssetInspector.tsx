import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import { mediaUrl } from "../../api/media-url";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useEscapeSurface } from "../../components/useEscapeSurface";
import { getAssetDisplayName } from "../library/libraryOrdering";
import { AssetContextMenu } from "./AssetContextMenu";
import { useAssetContextMenu } from "./useAssetContextMenu";
import { useDeletedAssetReconciliation } from "./useDeletedAssetReconciliation";
import type { AssetDetail, AssetSummary } from "../../api/types";

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
  findSimilarDisabled?: boolean;
}

type CopyStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type ActionFeedback = {
  kind: "success" | "error";
  message: string;
};

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
  findSimilarDisabled = false,
}: AssetInspectorProps) {
  const queryClient = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ["asset-detail", assetId],
    queryFn: () => client.getAssetDetail(assetId),
  });
  const reconcileDeletedAssets = useDeletedAssetReconciliation({
    inspectedAssetId: null,
    onCloseDetail: onClose,
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
  const [actionFeedback, setActionFeedback] = useState<ActionFeedback | null>(
    null,
  );
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const confirmationOpenerRef = useRef<HTMLElement | null>(null);
  const copyPendingRef = useRef(false);
  const mutationPendingRef = useRef(false);

  useEffect(() => {
    closeButtonRef.current?.focus({ preventScroll: true });
  }, []);

  useEscapeSurface(!confirmDelete && !confirmRemoveSource, onClose);

  const runClipboardCopy = async () => {
    if (copyPendingRef.current) return;
    copyPendingRef.current = true;
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
    } finally {
      copyPendingRef.current = false;
    }
  };

  const runCopyOriginal = async () => {
    if (copyPendingRef.current) return;
    copyPendingRef.current = true;
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
    } finally {
      copyPendingRef.current = false;
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
      setActionFeedback({
        kind: "success",
        message: "Opened the recorded Source Path in File Explorer.",
      });
    } catch (error) {
      setActionFeedback({
        kind: "error",
        message: tauriErrorDetail(
          error,
          "The requested file could not be opened in File Explorer. The Library was not modified.",
        ),
      });
    }
  };

  const runRemoveSource = async (sourcePath: string) => {
    if (mutationPendingRef.current) return;
    mutationPendingRef.current = true;
    setIsRemovingSource(true);
    try {
      const result = await client.removeSourceRecord(assetId, sourcePath);
      setConfirmRemoveSource(null);
      setActionFeedback({
        kind: "success",
        message: result.asset_deleted
          ? "Removed the final Source Record and deleted the Orphan Asset."
          : "Removed the Source Record.",
      });
      // A normal Source Record removal only refreshes the affected data: the
      // Asset and the current selection stay, the detail shows the remaining
      // Source Records.
      if (result.asset_deleted) {
        // The backend turned this Asset into an Orphan Asset and deleted it,
        // so it takes the same post-delete path as an explicit delete.
        if (onDeleted) onDeleted(assetId);
        else onClose();
        await reconcileDeletedAssets([assetId]);
      } else {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["assets"] }),
          queryClient.invalidateQueries({ queryKey: ["app-state"] }),
          queryClient.invalidateQueries({ queryKey: ["asset-detail", assetId] }),
        ]);
      }
    } catch (error) {
      setActionFeedback({
        kind: "error",
        message: tauriErrorDetail(
          error,
          "The requested Asset change could not be completed. The Library was not modified by the desktop UI.",
        ),
      });
      setConfirmRemoveSource(null);
    } finally {
      mutationPendingRef.current = false;
      setIsRemovingSource(false);
    }
  };

  const runConfirmedDelete = async () => {
    if (mutationPendingRef.current) return;
    mutationPendingRef.current = true;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await client.deleteAsset(assetId);
      setConfirmDelete(false);
      if (onDeleted) onDeleted(assetId);
      else onClose();
      // Shared post-delete coordination removes the Asset from the visible
      // wall, drops its detail cache, closes the detail while it still targets
      // this Asset, and then refreshes counts and App State.
      await reconcileDeletedAssets([assetId]);
    } catch (error) {
      setDeleteError(
        tauriErrorDetail(
          error,
          "The requested Asset change could not be completed. The Library was not modified by the desktop UI.",
        ),
      );
      setConfirmDelete(false);
    } finally {
      mutationPendingRef.current = false;
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
      setActionFeedback({
        kind: "success",
        message: "Find Similar will search from this Asset once Library search lands.",
      });
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
          ref={closeButtonRef}
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
          copyBusy={copyState.kind === "pending" || copyOriginalState.kind === "pending"}
          revealState={revealState}
          actionFeedback={actionFeedback}
          deleteError={deleteError}
          isDeleting={isDeleting}
          confirmDelete={confirmDelete}
          confirmRemoveSource={confirmRemoveSource}
          isRemovingSource={isRemovingSource}
          confirmationOpener={confirmationOpenerRef.current}
          onCopy={runClipboardCopy}
          onCopyOriginal={runCopyOriginal}
          onRevealManaged={runRevealManaged}
          onRevealSource={runRevealSource}
          onFindSimilar={handleFindSimilar}
          findSimilarDisabled={findSimilarDisabled}
          onRequestDelete={(opener) => {
            confirmationOpenerRef.current = opener;
            setDeleteError(null);
            setConfirmDelete(true);
          }}
          onCancelDelete={() => setConfirmDelete(false)}
          onConfirmDelete={runConfirmedDelete}
          onRequestRemoveSource={(sourcePath, opener) => {
            confirmationOpenerRef.current = opener;
            setConfirmRemoveSource(sourcePath);
          }}
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
  copyBusy,
  revealState,
  actionFeedback,
  deleteError,
  isDeleting,
  confirmDelete,
  confirmRemoveSource,
  isRemovingSource,
  confirmationOpener,
  onCopy,
  onCopyOriginal,
  onRevealManaged,
  onRevealSource,
  onFindSimilar,
  findSimilarDisabled,
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
  copyBusy: boolean;
  revealState: CopyStatus;
  actionFeedback: ActionFeedback | null;
  deleteError: string | null;
  isDeleting: boolean;
  confirmDelete: boolean;
  confirmRemoveSource: string | null;
  isRemovingSource: boolean;
  confirmationOpener: HTMLElement | null;
  onCopy: () => void;
  onCopyOriginal: () => void;
  onRevealManaged: () => void;
  onRevealSource: (sourcePath: string) => void;
  onFindSimilar: () => void;
  findSimilarDisabled: boolean;
  onRequestDelete: (opener: HTMLElement) => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  onRequestRemoveSource: (sourcePath: string, opener: HTMLElement) => void;
  onCancelRemoveSource: () => void;
  onConfirmRemoveSource: (sourcePath: string) => void;
}) {
  const name = assetName(asset);
  const preview = mediaUrl(asset.library_url);
  const previewMenu = useAssetContextMenu(copyBusy);
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
        {previewMenu.anchor && !copyBusy ? (
          <AssetContextMenu
            x={previewMenu.anchor.x}
            y={previewMenu.anchor.y}
            opener={previewMenu.anchor.opener}
            menuLabel={`Actions for ${name}`}
            items={[
              { label: "Copy image", onSelect: onCopy, disabled: copyBusy },
              { label: "Copy original file", onSelect: onCopyOriginal, disabled: copyBusy },
            ]}
            onClose={previewMenu.closeMenu}
          />
        ) : null}
        <div className="inspector-primary-actions">
          <button
            className="button"
            type="button"
            disabled={copyBusy}
            onClick={onCopy}
          >
            {copyPending ? "Copying…" : "Copy image"}
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={findSimilarDisabled}
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
                  onClick={(event) => onRequestRemoveSource(source.source_path, event.currentTarget)}
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
                disabled={copyBusy}
                onClick={onCopyOriginal}
              >
                {copyOriginalPending ? "Copying…" : "Copy original file"}
              </button>
              <button
                className="button button-danger"
                type="button"
                disabled={isDeleting}
                onClick={(event) => onRequestDelete(event.currentTarget)}
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
        <section
          className={`notice ${actionFeedback.kind === "error" ? "notice-warning" : "notice-success"}`}
          role={actionFeedback.kind === "error" ? "alert" : "status"}
          aria-label={actionFeedback.kind === "error" ? "Asset action failed" : undefined}
        >
          {actionFeedback.kind === "error" ? <strong>Asset action failed</strong> : null}
          <span>{actionFeedback.message}</span>
        </section>
      ) : null}
      {deleteError ? (
        <section className="notice notice-warning" role="alert">
          <strong>Delete failed</strong>
          <span>{deleteError}</span>
        </section>
      ) : null}

      {confirmDelete ? (
        <ConfirmDialog
          titleId="inspector-delete-title"
          title="Delete this Asset?"
          detail="This deletes its Library Copy, Source Records, and Derived Artifacts. This cannot be undone."
          confirmLabel="Delete Asset"
          pending={isDeleting}
          onCancel={onCancelDelete}
          onConfirm={onConfirmDelete}
          onEscape={onCancelDelete}
          restoreFocusTo={confirmationOpener}
        />
      ) : null}

      {confirmRemoveSource ? (
        <ConfirmDialog
          titleId="inspector-remove-source-title"
          title="Remove this Source Record?"
          detail="If this is the final Source Record, MemeSort deletes the resulting Orphan Asset and its Derived Artifacts."
          confirmLabel="Remove Source Record"
          pending={isRemovingSource}
          onCancel={onCancelRemoveSource}
          onConfirm={() => onConfirmRemoveSource(confirmRemoveSource)}
          onEscape={onCancelRemoveSource}
          restoreFocusTo={confirmationOpener}
        />
      ) : null}
    </div>
  );
}
