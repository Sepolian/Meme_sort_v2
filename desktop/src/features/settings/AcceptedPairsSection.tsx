import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";

/**
 * Settings > Accepted Duplicate Pairs reset (ticket 16).
 *
 * Clears all Accepted Duplicate Pair decisions without deleting Assets or
 * Derived Artifacts. After success the duplicate view refreshes through
 * query invalidation so a later scan may show the cleared pairs again.
 */
export function AcceptedPairsSection({
  client,
  onStateChanged,
}: {
  client: MemeSortClient;
  onStateChanged?: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [isWorking, setIsWorking] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const clear = async () => {
    setIsWorking(true);
    setFeedback(null);
    setError(null);
    try {
      const result = await client.clearAcceptedPairs();
      setFeedback(
        `Cleared ${result.cleared_pairs} Accepted Duplicate Pair${result.cleared_pairs === 1 ? "" : "s"}. Future duplicate scans may show those pairs again. Assets were not deleted.`,
      );
      setConfirming(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["app-state"] }),
      ]);
      onStateChanged?.();
    } catch (clearError) {
      setError(tauriErrorDetail(clearError, "MemeSort could not clear Accepted Duplicate Pairs. No Assets were deleted; retry when the sidecar is available."));
      setConfirming(false);
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <>
      <p>
        Accepted Duplicate Pair management lives here. Pairs are unordered, excluded from
        future duplicate review, and removed when either Asset is deleted.
      </p>
      <p>Clearing decisions never deletes Assets or Derived Artifacts; cleared pairs may appear in a later scan.</p>
      <div className="import-actions">
        <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => setConfirming(true)}>
          Clear accepted pairs
        </button>
      </div>
      {feedback ? <p role="status">{feedback}</p> : null}
      {error ? (
        <section className="notice notice-warning" role="alert">
          <strong>Could not clear Accepted Duplicate Pairs</strong>
          <span>{error}</span>
          <div className="import-actions">
            <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => void clear()}>Retry clear</button>
          </div>
        </section>
      ) : null}
      {confirming ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={isWorking ? undefined : () => setConfirming(false)}>
          <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="clear-accepted-pairs-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">Confirm change</p>
            <h2 id="clear-accepted-pairs-title">Clear all Accepted Duplicate Pairs?</h2>
            <p>This clears every Keep Both decision without deleting Assets or Derived Artifacts. Cleared pairs may appear in a later duplicate scan.</p>
            <div className="dialog-actions">
              <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => setConfirming(false)}>Cancel</button>
              <button className="button button-danger" type="button" autoFocus disabled={isWorking} onClick={() => void clear()}>{isWorking ? "Working…" : "Clear accepted pairs"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

export default AcceptedPairsSection;
