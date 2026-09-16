import type { AppState, ImportTask } from "../../api/types";
import {
  IMPORT_TERMINAL_STATUSES,
  importNoticePresentation,
  importProgressMessage,
  importWorkIsActive,
} from "../import/import-status";

export type TaskTone = "active" | "attention";
export type RuntimeTaskStatus = "idle" | "checking" | "healthy" | "failed";

export interface TaskSummary {
  visible: boolean;
  tone: TaskTone | null;
  attention: boolean;
  compactLabel: string | null;
  importLabel: string | null;
  indexingLabel: string | null;
  indexingAttention: boolean;
  healthLabel: string | null;
}

const ATTENTION_IMPORT_STATUSES = new Set(["completed_with_errors", "failed", "cancelled"]);

function importLabelFor(snapshot: ImportTask | null): { label: string | null; attention: boolean } {
  if (!snapshot || snapshot.batch_id === null) return { label: null, attention: false };
  if (importWorkIsActive(snapshot)) {
    const message = importProgressMessage(snapshot).trim();
    if (message) return { label: message, attention: false };
    return { label: "Import Batch running", attention: false };
  }
  if (!IMPORT_TERMINAL_STATUSES.has(snapshot.status)) {
    // Non-terminal without running flag (e.g. paused overlay uses running=true,
    // so this is a fallback for unexpected states).
    return { label: null, attention: false };
  }
  if (snapshot.status === "completed") return { label: null, attention: false };
  if (ATTENTION_IMPORT_STATUSES.has(snapshot.status)) {
    const presentation = importNoticePresentation(snapshot.status, true);
    return { label: presentation.heading, attention: true };
  }
  return { label: null, attention: false };
}

function indexingLabelFor(appState: AppState | null): { label: string | null; attention: boolean } {
  if (!appState) return { label: null, attention: false };
  const worker = appState.worker_loop;
  const pending = appState.library_status.job_counts.pending ?? appState.pending_jobs.length;
  const running = appState.library_status.job_counts.running ?? 0;
  const failedCount = appState.library_status.job_counts.failed ?? 0;
  const runningLabel = running === 1 ? "1 running job" : `${running} running jobs`;
  const pendingLabel = pending === 1 ? "1 pending job" : `${pending} pending jobs`;
  const failedLabel = failedCount === 1 ? "1 failed job needs retry" : `${failedCount} failed jobs need retry`;
  if (worker.paused && (pending > 0 || running > 0 || failedCount > 0)) {
    const labels = ["Indexing paused"];
    if (running > 0) labels.push(runningLabel);
    if (pending > 0) labels.push(pendingLabel);
    if (failedCount > 0) labels.push(failedLabel);
    return {
      label: labels.join(" · "),
      attention: true,
    };
  }
  const labels: string[] = [];
  if (running > 0) labels.push(runningLabel);
  if (pending > 0) labels.push(pendingLabel);
  if (failedCount > 0) labels.push(failedLabel);
  return { label: labels.length > 0 ? `Indexing ${labels.join(" · ")}` : null, attention: failedCount > 0 };
}

function healthLabelFor(status: RuntimeTaskStatus, blocked: boolean): { label: string | null; attention: boolean } {
  if (status === "checking") return { label: "Preparing search…", attention: false };
  if (status === "failed" || blocked) return { label: "Runtime needs attention", attention: true };
  return { label: null, attention: false };
}

export function summarizeTasks(args: {
  importTask: ImportTask | null;
  healthStatus: RuntimeTaskStatus;
  healthBlocked: boolean;
  appState: AppState | null;
}): TaskSummary {
  const importPart = importLabelFor(args.importTask);
  const indexingPart = indexingLabelFor(args.appState);
  const healthPart = healthLabelFor(args.healthStatus, args.healthBlocked);

  const parts = [importPart.label, indexingPart.label, healthPart.label].filter((part): part is string => part !== null);
  const visible = parts.length > 0;
  const attention = importPart.attention || indexingPart.attention || healthPart.attention;
  return {
    visible,
    tone: !visible ? null : attention ? "attention" : "active",
    attention,
    compactLabel: visible ? parts.join(" · ") : null,
    importLabel: importPart.label,
    indexingLabel: indexingPart.label,
    indexingAttention: indexingPart.attention,
    healthLabel: healthPart.label,
  };
}
