import { useImportBatch } from "./ImportBatchContext";
import {
  importAssetTotals,
  importBatchIsTerminal,
  importCommittedAssets,
} from "./import-status";

export function ImportFailureDetails() {
  const batch = useImportBatch();
  const snapshot = batch.snapshot;
  if (!snapshot || !importBatchIsTerminal(snapshot)) return null;
  const summary = snapshot.result ?? snapshot.partial_result;
  const error = snapshot.error;
  const fatalError = snapshot.status === "failed" ? error : null;
  const hasRecordedFailures = Boolean(summary && summary.failure_count > 0);
  if (!hasRecordedFailures && !fatalError) return null;

  const stoppedEarly = snapshot.status === "failed" || snapshot.status === "cancelled";
  const committed = summary ? importCommittedAssets(summary) : false;
  const failureCount = summary?.failure_count || 1;
  const failures = summary?.failure_details.length
    ? summary.failure_details
    : fatalError
      ? [{ stage: "batch", code: fatalError.error, source_name: "Import Batch", detail: fatalError.detail }]
      : [];
  const omitted = Math.max(0, failureCount - failures.length);

  return (
    <section className="import-failure-details" aria-labelledby="import-failure-details-heading">
      <h2 id="import-failure-details-heading">Import Failure details</h2>
      <p>
        {failureCount} Import Failure(s) recorded.
        {summary
          ? committed
            ? ` ${stoppedEarly ? "Committed before stopping" : "Committed"}: ${importAssetTotals(summary)}.`
            : " No Assets were committed by this batch."
          : " No Assets were committed by this batch."}
        {summary && fatalError ? ` ${fatalError.detail}` : ""}
      </p>
      <ul className="detail-list">
        {failures.map((failure, index) => (
          <li key={`${index}-${failure.source_name}`}>
            <span className="mono">{failure.source_name}</span>
            <span>{failure.stage} · {failure.code} · {failure.detail}</span>
          </li>
        ))}
      </ul>
      {omitted > 0 ? (
        <p>
          Showing the first {failures.length} of {failureCount} failures;
          {" "}{omitted} more were omitted.
        </p>
      ) : null}
    </section>
  );
}
