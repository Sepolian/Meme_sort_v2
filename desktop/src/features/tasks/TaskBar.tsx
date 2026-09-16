import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { AppState } from "../../api/types";
import { useImportBatch } from "../import/ImportBatchContext";
import { ImportFailureDetails } from "../import/ImportFailureDetails";
import {
  importBatchIsTerminal,
  importNoticePresentation,
  importProgressMessage,
  importResultMessage,
} from "../import/import-status";
import { useRuntimeHealth } from "../runtime/useRuntimeHealth";
import { summarizeTasks } from "./taskVisibility";

const MINIMIZED_STORAGE_KEY = "memesort.taskbar.minimized";
const SUCCESS_NOTICE_VISIBLE_MS = 8_000;

function readMinimized(): boolean {
  try {
    return window.localStorage.getItem(MINIMIZED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Compact Activity entry for import, indexing, and Runtime health.
 *
 * Visible only for active, recently completed, failed, or otherwise actionable
 * work. Returns null for idle/non-actionable state so it automatically
 * disappears. Collapsing details never hides attention-required work or its
 * recovery route.
 */
export function TaskBar({ appState }: { appState: AppState | null }) {
  const batch = useImportBatch();
  const health = useRuntimeHealth();
  const [minimized, setMinimized] = useState<boolean>(() => readMinimized());
  const [, setDismissTick] = useState(0);
  const dismissedNoticesRef = useRef(new Set<string>());

  const summary = summarizeTasks({
    importTask: batch.snapshot,
    healthStatus: health.status,
    healthBlocked: health.isBlocked,
    appState,
  });

  const snapshot = batch.snapshot;
  const hasBatch = snapshot !== null && snapshot.batch_id !== null;
  const terminal = snapshot !== null && hasBatch && importBatchIsTerminal(snapshot);
  const noticeKey = hasBatch && snapshot ? `${snapshot.batch_id}:${snapshot.status}` : null;
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

  const showImportNotice = Boolean(snapshot && hasBatch && !dismissed);
  const activityVisible = summary.visible || showImportNotice;
  if (!activityVisible || (!summary.compactLabel && !showImportNotice)) return null;

  const setMinimizedPersisted = (next: boolean) => {
    setMinimized(next);
    try {
      window.localStorage.setItem(MINIMIZED_STORAGE_KEY, next ? "1" : "0");
    } catch {
      // Storage is a convenience only; the bar still toggles in memory.
    }
  };

  const details: Array<{ label: string; text: string }> = [];
  if (summary.indexingLabel) details.push({ label: "Indexing", text: summary.indexingLabel });
  if (summary.healthLabel) details.push({ label: "Runtime health", text: summary.healthLabel });

  const importPresentation = snapshot && terminal
    ? importNoticePresentation(snapshot.status, true)
    : null;
  const importMessage = snapshot && hasBatch
    ? terminal
      ? importResultMessage(snapshot)
      : importProgressMessage(snapshot)
    : "";
  const importFailureSummary = snapshot?.result ?? snapshot?.partial_result;
  const showFailureDetails = Boolean(
    snapshot && terminal && (
      (importFailureSummary && importFailureSummary.failure_count > 0)
      || (snapshot.status === "failed" && snapshot.error)
    ),
  );

  return (
    <section className="task-bar" role="region" aria-label="Activity" data-tone={summary.tone ?? "active"}>
      <div className="task-bar-header">
        <div className="task-bar-summary">
          <strong>Activity</strong>
          <span>{summary.compactLabel ?? importPresentation?.heading}</span>
        </div>
        <div className="task-bar-actions">
          <button
            className="button button-secondary task-bar-toggle"
            type="button"
            aria-controls="activity-details"
            aria-expanded={!minimized}
            onClick={() => setMinimizedPersisted(!minimized)}
          >
            {minimized ? "Expand Activity" : "Collapse Activity"}
          </button>
          {showFailureDetails && minimized ? (
            <Link
              className="text-button"
              to="/"
              onClick={() => setMinimizedPersisted(false)}
            >
              View Import Failure details
            </Link>
          ) : null}
          <Link className="text-button" to="/settings">
            Open diagnostics
          </Link>
        </div>
      </div>
      <div id="activity-details" hidden={minimized}>
        {details.length ? (
          <ul className="detail-list task-bar-details">
            {details.map((detail) => (
              <li key={detail.label}>
                <strong>{detail.label}</strong>
                <span>{detail.text}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {showImportNotice && snapshot && hasBatch ? (
          <section
            className={`notice ${importPresentation ? noticeClass(importPresentation.severity) : "notice-success"}`}
            aria-label={importPresentation?.heading ?? "Import Batch progress"}
          >
            <p
              role={importPresentation?.role ?? "status"}
              aria-label={terminal ? "Import Batch result" : "Import Batch progress"}
            >
              {importMessage}
            </p>
            {showFailureDetails && !minimized ? (
              <Link className="text-button" to="/">View Import Failure details</Link>
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
            {showFailureDetails ? <ImportFailureDetails /> : null}
          </section>
        ) : null}
      </div>
      {summary.indexingLabel ? (
        <span
          className="activity-live-summary"
          role={summary.indexingAttention ? "alert" : "status"}
          aria-label="Indexing activity"
          aria-live={summary.indexingAttention ? "assertive" : "polite"}
          aria-atomic="true"
        >
          {summary.indexingLabel}
        </span>
      ) : null}
    </section>
  );
}

function noticeClass(severity: "success" | "warning" | "fatal"): string {
  return severity === "fatal" ? "notice-danger" : `notice-${severity}`;
}
