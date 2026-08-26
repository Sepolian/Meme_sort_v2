import { useImportBatch } from "./ImportBatchContext";
import {
  importAssetTotals,
  importBatchIsTerminal,
  importCommittedAssets,
} from "./import-status";

export function ImportFailureDetails() {
  const batch = useImportBatch();
  const snapshot = batch?.snapshot ?? null;
  if (!snapshot || !importBatchIsTerminal(snapshot)) return null;
  const summary = snapshot.result ?? snapshot.partial_result;
  if (!summary || summary.failure_count === 0) return null;

  const stoppedEarly = snapshot.status === "failed" || snapshot.status === "cancelled";
  const committed = importCommittedAssets(summary);
  const omitted = summary.failure_count - summary.failure_details.length;

  return (
    <section className="import-failure-details" aria-labelledby="import-failure-details-heading">
      <h2 id="import-failure-details-heading">Import Failure details</h2>
      <p>
        {summary.failure_count} Import Failure(s) recorded.
        {committed
          ? ` ${stoppedEarly ? "Committed before stopping" : "Committed"}: ${importAssetTotals(summary)}.`
          : " No Assets were committed by this batch."}
      </p>
      <ul className="detail-list">
        {summary.failure_details.map((failure, index) => (
          <li key={`${index}-${failure.source_name}`}>
            <span className="mono">{failure.source_name}</span>
            <span>{failure.stage} · {failure.code} · {failure.detail}</span>
          </li>
        ))}
      </ul>
      {omitted > 0 ? (
        <p>
          Showing the first {summary.failure_details.length} of {summary.failure_count} failures;
          {" "}{omitted} more were omitted.
        </p>
      ) : null}
    </section>
  );
}
