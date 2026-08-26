import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useImportBatch } from "./ImportBatchContext";
import {
  importBatchIsTerminal,
  importNoticePresentation,
  importProgressMessage,
  importResultMessage,
} from "./import-status";

const SUCCESS_NOTICE_VISIBLE_MS = 8_000;

function noticeClass(severity: "success" | "warning" | "fatal"): string {
  return severity === "fatal" ? "notice-danger" : `notice-${severity}`;
}

export function ImportBatchPanel() {
  const batch = useImportBatch();
  const [, setDismissTick] = useState(0);
  const dismissedNoticesRef = useRef(new Set<string>());

  const snapshot = batch?.snapshot ?? null;
  const hasBatch = snapshot !== null && snapshot.batch_id !== null;
  const noticeKey = hasBatch && snapshot ? `${snapshot.batch_id}:${snapshot.status}` : null;
  const terminal = snapshot !== null && hasBatch && importBatchIsTerminal(snapshot);
  const autoDismisses = terminal && snapshot?.status === "completed";
  const dismissed = noticeKey !== null && dismissedNoticesRef.current.has(noticeKey);

  useEffect(() => {
    if (!noticeKey || dismissed || !autoDismisses) return;
    const timer = window.setTimeout(() => {
      dismissedNoticesRef.current.add(noticeKey);
      setDismissTick((tick) => tick + 1);
    }, SUCCESS_NOTICE_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [noticeKey, dismissed, autoDismisses]);

  if (!batch || !snapshot || !hasBatch || dismissed) return null;

  const message = terminal
    ? importResultMessage(snapshot)
    : importProgressMessage(snapshot);
  const presentation = importNoticePresentation(snapshot.status, terminal);
  const summary = snapshot.result ?? snapshot.partial_result;
  const showDetailsLink = terminal
    && presentation.severity !== "success"
    && (summary?.failure_count ?? 0) > 0;

  return (
    <section className={`notice ${noticeClass(presentation.severity)}`} aria-label={presentation.heading}>
      <strong>{presentation.heading}</strong>
      <p
        role={presentation.role}
        aria-label={terminal ? "Import Batch result" : "Import Batch progress"}
      >
        {message}
      </p>
      {showDetailsLink ? (
        <Link className="text-button" to="/">View Import Failure details in the Library</Link>
      ) : null}
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
