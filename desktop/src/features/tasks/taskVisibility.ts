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
  if (worker.paused) {
    return {
      label: pending > 0 ? `Indexing paused · ${pending} pending jobs` : "Indexing paused",
      attention: true,
    };
  }
  if (pending > 0) {
    return { label: pending === 1 ? "Indexing 1 pending job" : `Indexing ${pending} pending jobs`, attention: false };
  }
  const failedRecent = (appState.library_status.recent_jobs ?? []).filter((job) => job.status === "failed");
  if (failedRecent.length > 0) {
    return {
      label: failedRecent.length === 1 ? "1 failed job needs retry" : `${failedRecent.length} failed jobs need retry`,
      attention: true,
    };
  }
  return { label: null, attention: false };
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
    healthLabel: healthPart.label,
  };
}
