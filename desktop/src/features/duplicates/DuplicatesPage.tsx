import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import { mediaUrl } from "../../api/media-url";
import type { AssetSummary, DuplicatePair } from "../../api/types";
import {
  buildAssetSummaryMap,
  composeDuplicatePairs,
  type ComposedDuplicatePair,
} from "../../api/result-models";
import { useRuntimeHealth } from "../runtime/useRuntimeHealth";
import { EmptyState } from "../../components/States";

/**
 * Redesigned Duplicates workflow (ticket 16).
 *
 * Comparison metadata source contract (documented for acceptance):
 * - dimensions (`width`/`height`) and Source Record counts
 *   (`source_record_count`) come from the current `AssetSummary` map loaded
 *   via `getAssets` and joined through ticket 05 `composeDuplicatePairs`;
 * - similarity `score` and Matched Frame references
 *   (`asset_a_matched_source_ref` / `asset_b_matched_source_ref`) stay on the
 *   `DuplicatePair` retrieval projection and are preserved verbatim.
 *
 * Pairs missing either current summary are omitted with recoverable stale
 * feedback (never a broken comparison). Keep Left deletes the right Asset and
 * Keep Right deletes the left Asset through `deleteAsset`, which relies on
 * ticket 02 `ON DELETE CASCADE` to remove acceptance rows for the deleted
 * Asset. Keep Both persists via `acceptDuplicatePair` and removes the pair
 * from the current view; the Python exclusion keeps it out of later scans
 * until `clearAcceptedPairs` runs.
 */

type KeepSide = "left" | "right";
type PairAction = "keep-left" | "keep-right" | "keep-both";

interface PairFailure {
  message: string;
  action: PairAction;
}

interface DeleteConfirmation {
  pair: ComposedDuplicatePair;
  side: KeepSide;
}

function pairKey(assetAId: string, assetBId: string): string {
  return `${assetAId}__${assetBId}`;
}

function dimensionsLabel(asset: AssetSummary): string {
  return asset.width && asset.height ? `${asset.width} \u00D7 ${asset.height}` : "Dimensions unavailable";
}

function sourceCountLabel(asset: AssetSummary): string {
  return `${asset.source_record_count} Source Record${asset.source_record_count === 1 ? "" : "s"}`;
}

function previewUrl(asset: AssetSummary): string | undefined {
  return mediaUrl(asset.library_url) ?? mediaUrl(asset.thumbnail_url);
}

function survivorAndDeleted(pair: ComposedDuplicatePair, side: KeepSide): { survivor: AssetSummary; deleted: AssetSummary } {
  return side === "left"
    ? { survivor: pair.assetA, deleted: pair.assetB }
    : { survivor: pair.assetB, deleted: pair.assetA };
}

export function DuplicatesPage({ client }: { client: MemeSortClient }) {
  const health = useRuntimeHealth();
  const queryClient = useQueryClient();
  const [threshold, setThreshold] = useState("0.92");
  const [rawPairs, setRawPairs] = useState<DuplicatePair[] | null>(null);
  const [summaries, setSummaries] = useState<AssetSummary[] | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [pairFailures, setPairFailures] = useState<Record<string, PairFailure>>({});
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<DeleteConfirmation | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const thresholdValue = Number(threshold);
  const isValidThreshold = Number.isFinite(thresholdValue) && thresholdValue >= 0 && thresholdValue <= 1;
  const semanticBlocked = health.isBlocked;

  const composed = useMemo(() => {
    if (!rawPairs || !summaries) return null;
    return composeDuplicatePairs(rawPairs, buildAssetSummaryMap(summaries));
  }, [rawPairs, summaries]);

  const scan = async () => {
    if (!isValidThreshold || isScanning) return;
    setIsScanning(true);
    setScanError(null);
    setNotice(null);
    try {
      const [duplicates, assets] = await Promise.all([
        client.getDuplicates(thresholdValue),
        client.getAssets(),
      ]);
      setRawPairs(duplicates.pairs);
      setSummaries(assets.assets);
      setPairFailures({});
    } catch (error) {
      setScanError(tauriErrorDetail(error, "MemeSort could not scan for duplicate Assets."));
    } finally {
      setIsScanning(false);
    }
  };

  const removeRawPairsWithAsset = (assetId: string) => {
    setRawPairs((current) =>
      current === null
        ? current
        : current.filter((pair) => pair.asset_a_id !== assetId && pair.asset_b_id !== assetId),
    );
  };

  const removeRawPair = (assetAId: string, assetBId: string) => {
    setRawPairs((current) =>
      current === null
        ? current
        : current.filter((pair) => !(pair.asset_a_id === assetAId && pair.asset_b_id === assetBId)
          && !(pair.asset_a_id === assetBId && pair.asset_b_id === assetAId)),
    );
  };

  const runKeepBoth = async (pair: ComposedDuplicatePair) => {
    const key = pairKey(pair.assetA.asset_id, pair.assetB.asset_id);
    setPendingKey(key);
    setNotice(null);
    setPairFailures((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    try {
      await client.acceptDuplicatePair(pair.assetA.asset_id, pair.assetB.asset_id);
      removeRawPair(pair.assetA.asset_id, pair.assetB.asset_id);
      setPairFailures((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setNotice(`Kept both ${pair.assetA.library_path} and ${pair.assetB.library_path} as an Accepted Duplicate Pair.`);
    } catch (error) {
      setPairFailures((current) => ({
        ...current,
        [key]: {
          message: tauriErrorDetail(error, "MemeSort could not keep both Assets. The pair remains visible; retry when the sidecar is available."),
          action: "keep-both",
        },
      }));
    } finally {
      setPendingKey(null);
    }
  };

  const confirmKeepSide = (pair: ComposedDuplicatePair, side: KeepSide) => {
    setConfirmation({ pair, side });
  };

  const runConfirmedDelete = async () => {
    if (!confirmation || pendingKey) return;
    const { pair, side } = confirmation;
    const { survivor, deleted } = survivorAndDeleted(pair, side);
    const key = pairKey(pair.assetA.asset_id, pair.assetB.asset_id);
    setPendingKey(key);
    try {
      await client.deleteAsset(deleted.asset_id);
      // Ticket 02 cascade removes accepted-pair rows for the deleted Asset;
      // no separate clear call is needed or issued here.
      removeRawPairsWithAsset(deleted.asset_id);
      setSummaries((current) =>
        current === null ? current : current.filter((asset) => asset.asset_id !== deleted.asset_id),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["app-state"] }),
      ]);
      setPairFailures((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setNotice(`Kept ${survivor.library_path} and deleted ${deleted.library_path}.`);
      setConfirmation(null);
    } catch (error) {
      const action: PairAction = side === "left" ? "keep-left" : "keep-right";
      setPairFailures((current) => ({
        ...current,
        [key]: {
          message: tauriErrorDetail(error, "MemeSort could not delete the Asset. The pair remains visible; retry when the sidecar is available."),
          action,
        },
      }));
      setConfirmation(null);
    } finally {
      setPendingKey(null);
    }
  };

  const retryPair = (pair: ComposedDuplicatePair, failure: PairFailure) => {
    if (failure.action === "keep-both") {
      void runKeepBoth(pair);
      return;
    }
    confirmKeepSide(pair, failure.action === "keep-left" ? "left" : "right");
  };

  const confirmationDetail = confirmation
    ? (() => {
        const { survivor, deleted } = survivorAndDeleted(confirmation.pair, confirmation.side);
        const keepLabel = confirmation.side === "left" ? "Keep Left" : "Keep Right";
        return {
          title: `${keepLabel}: keep ${survivor.library_path}?`,
          detail: `This keeps ${survivor.library_path} and deletes ${deleted.library_path} with its ${deleted.source_record_count} Source Record${deleted.source_record_count === 1 ? "" : "s"}. Accepted Duplicate Pair records for the deleted Asset are removed by cascade. This cannot be undone.`,
          confirmLabel: confirmation.side === "left" ? "Keep Left" : "Keep Right",
        };
      })()
    : null;

  return (
    <main className="page" aria-labelledby="page-title">
      <div className="page-heading">
        <p className="eyebrow">Library maintenance</p>
        <h1 id="page-title">Duplicate assets</h1>
      </div>
      <section className="surface search-panel">
        <h2>Duplicate review</h2>
        <p>Compare Indexed Asset pairs from the Active Index Recipe. GIF matches use the strongest frame-to-frame score.</p>
        <p>Dimensions and Source Record counts come from the current Asset list; score and Matched Frames come from the duplicate scan.</p>
        <label htmlFor="duplicate-threshold">Duplicate threshold</label>
        <div className="search-form">
          <input id="duplicate-threshold" type="number" min="0" max="1" step="0.01" value={threshold} onChange={(event) => setThreshold(event.target.value)} />
          <button className="button" type="button" disabled={isScanning || !isValidThreshold || semanticBlocked} onClick={() => void scan()}>
            Scan duplicates
          </button>
        </div>
        {!isValidThreshold ? <p role="alert">Enter a threshold between 0 and 1.</p> : null}
        {semanticBlocked ? <p>Duplicate review is unavailable until the current session passes the Runtime health check. Library browsing and import still work.</p> : null}
      </section>
      {isScanning ? <p aria-live="polite">Scanning Indexed Assets for duplicate pairs…</p> : null}
      {scanError ? (
        <section className="notice notice-warning" role="alert">
          <strong>Duplicate scan unavailable</strong>
          <span>{scanError}</span>
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => void scan()}>Retry scan</button>
          </div>
        </section>
      ) : null}
      {notice ? (
        <section className="notice notice-success" role="status"><span>{notice}</span></section>
      ) : null}
      {composed && composed.stale.length ? (
        <section className="notice notice-warning" role="status" aria-label="Stale duplicate pairs">
          <strong>{composed.stale.length} duplicate pair{composed.stale.length === 1 ? "" : "s"} omitted</strong>
          <span>Their Assets are no longer in the current Asset list. Rescan to refresh; nothing was rendered with invented dimensions or Source Records.</span>
          <div className="import-actions">
            <button className="button button-secondary" type="button" onClick={() => void scan()}>Rescan duplicates</button>
          </div>
        </section>
      ) : null}
      {composed ? (
        composed.pairs.length ? (
          <section className="duplicate-pairs" aria-label="Duplicate pairs" role="region">
            {composed.pairs.map((pair) => {
              const key = pairKey(pair.assetA.asset_id, pair.assetB.asset_id);
              const failure = pairFailures[key];
              const isPending = pendingKey === key;
              return (
                <article className="duplicate-pair duplicate-pair-composed" key={key} aria-label={`Duplicate pair ${pair.assetA.library_path} and ${pair.assetB.library_path}`}>
                  <header>
                    <strong>Similarity score {pair.score.toFixed(3)}</strong>
                    <span>Threshold {thresholdValue.toFixed(2)}</span>
                  </header>
                  <div className="duplicate-assets">
                    <div aria-label={`Left Asset ${pair.assetA.library_path}`}>
                      {previewUrl(pair.assetA) ? <img src={previewUrl(pair.assetA)!} alt={`${pair.assetA.library_path} preview`} /> : <div className="media-placeholder" aria-hidden="true" />}
                      <strong>{pair.assetA.library_path}</strong>
                      <p>{dimensionsLabel(pair.assetA)} · {sourceCountLabel(pair.assetA)}</p>
                      {pair.assetAMatchedSourceRef ? <p>Matched frame: {pair.assetAMatchedSourceRef}</p> : <p>No matched frame</p>}
                    </div>
                    <div aria-label={`Right Asset ${pair.assetB.library_path}`}>
                      {previewUrl(pair.assetB) ? <img src={previewUrl(pair.assetB)!} alt={`${pair.assetB.library_path} preview`} /> : <div className="media-placeholder" aria-hidden="true" />}
                      <strong>{pair.assetB.library_path}</strong>
                      <p>{dimensionsLabel(pair.assetB)} · {sourceCountLabel(pair.assetB)}</p>
                      {pair.assetBMatchedSourceRef ? <p>Matched frame: {pair.assetBMatchedSourceRef}</p> : <p>No matched frame</p>}
                    </div>
                  </div>
                  {failure ? (
                    <section className="notice notice-warning" role="alert" aria-label={`Action failed for ${pair.assetA.library_path} and ${pair.assetB.library_path}`}>
                      <strong>Action failed</strong>
                      <span>{failure.message}</span>
                      <div className="import-actions">
                        <button className="button button-secondary" type="button" disabled={isPending} onClick={() => retryPair(pair, failure)}>Retry</button>
                      </div>
                    </section>
                  ) : null}
                  <div className="import-actions" aria-label="Duplicate resolution actions">
                    <button className="button button-secondary" type="button" disabled={isPending} onClick={() => confirmKeepSide(pair, "left")}>Keep Left</button>
                    <button className="button button-secondary" type="button" disabled={isPending} onClick={() => confirmKeepSide(pair, "right")}>Keep Right</button>
                    <button className="button" type="button" disabled={isPending} onClick={() => void runKeepBoth(pair)}>{isPending ? "Working…" : "Keep Both"}</button>
                  </div>
                </article>
              );
            })}
          </section>
        ) : (
          <EmptyState title="No duplicate pairs found" detail="Lower the threshold or index more Assets, then scan again." />
        )
      ) : rawPairs === null ? null : (
        <EmptyState title="No duplicate pairs found" detail="Lower the threshold or index more Assets, then scan again." />
      )}
      {confirmation && confirmationDetail ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={pendingKey ? undefined : () => setConfirmation(null)}>
          <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="duplicate-keep-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">Confirm change</p>
            <h2 id="duplicate-keep-title">{confirmationDetail.title}</h2>
            <p>{confirmationDetail.detail}</p>
            <div className="dialog-actions">
              <button className="button button-secondary" type="button" disabled={pendingKey !== null} onClick={() => setConfirmation(null)}>Cancel</button>
              <button className="button button-danger" type="button" autoFocus disabled={pendingKey !== null} onClick={() => void runConfirmedDelete()}>{pendingKey ? "Working…" : confirmationDetail.confirmLabel}</button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

export default DuplicatesPage;
