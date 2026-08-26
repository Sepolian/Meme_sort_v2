import { useImportBatch } from "./ImportBatchContext";
import {
  importBatchIsTerminal,
  importProgressMessage,
  importResultMessage,
} from "./import-status";

export function ImportBatchPanel() {
  const batch = useImportBatch();
  if (!batch || !batch.snapshot || batch.snapshot.batch_id === null) return null;
  const snapshot = batch.snapshot;

  const terminal = importBatchIsTerminal(snapshot);
  const message = terminal
    ? importResultMessage(snapshot)
    : importProgressMessage(snapshot);
  const noticeKind = !terminal
    ? "notice-success"
    : snapshot.status === "completed"
      ? "notice-success"
      : "notice-warning";

  return (
    <section className={`notice ${noticeKind}`} aria-label="Import Batch">
      <strong>{terminal ? "Import Batch result" : "Import Batch"}</strong>
      <p role="status" aria-label="Import Batch progress">{message}</p>
      {!terminal ? (
        <div className="import-actions">
          <button
            className="button button-secondary"
            type="button"
            disabled={batch.controlsPending || !snapshot.running || snapshot.paused || snapshot.pause_requested}
            onClick={() => void batch.requestPause()}
          >
            Pause Import Batch
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={batch.controlsPending || !snapshot.paused}
            onClick={() => void batch.requestResume()}
          >
            Resume Import Batch
          </button>
        </div>
      ) : null}
    </section>
  );
}
