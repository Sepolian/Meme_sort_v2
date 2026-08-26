import type { ImportBatchStatus, ImportTask } from "../../api/types";

export const IMPORT_TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
]);

export type ImportNoticeSeverity = "success" | "warning" | "fatal";

export function importWorkIsActive(
  snapshot: ImportTask | null | undefined,
): boolean {
  return Boolean(
    snapshot?.running && !IMPORT_TERMINAL_STATUSES.has(snapshot.status),
  );
}

export function importBatchIsTerminal(snapshot: ImportTask): boolean {
  return (
    !snapshot.running && IMPORT_TERMINAL_STATUSES.has(snapshot.status)
  );
}

export interface ImportNoticePresentation {
  heading: string;
  severity: ImportNoticeSeverity;
  role: "status" | "alert";
}

export function importNoticePresentation(
  status: ImportBatchStatus,
  terminal: boolean,
): ImportNoticePresentation {
  if (!terminal) {
    return { heading: "Import Batch", severity: "success", role: "status" };
  }
  switch (status) {
    case "completed":
      return { heading: "Import Batch completed", severity: "success", role: "status" };
    case "completed_with_errors":
      return { heading: "Import Batch finished with errors", severity: "warning", role: "alert" };
    case "failed":
      return { heading: "Import Batch failed", severity: "fatal", role: "alert" };
    case "cancelled":
      return { heading: "Import Batch cancelled", severity: "warning", role: "status" };
    default:
      return { heading: "Import Batch", severity: "success", role: "status" };
  }
}

export function importAssetTotals(
  summary: NonNullable<ImportTask["result"]>,
): string {
  return `${summary.new_assets} new Asset${summary.new_assets === 1 ? "" : "(s)"} and ${summary.duplicate_assets} duplicate Asset${summary.duplicate_assets === 1 ? "" : "(s)"}`;
}

export function importCommittedAssets(
  summary: NonNullable<ImportTask["result"]>,
): boolean {
  return summary.new_assets + summary.duplicate_assets > 0;
}

export function importProgressMessage(snapshot: ImportTask): string {
  switch (snapshot.status) {
    case "scanning":
      return [
        `Scanning ${snapshot.selected_sources} selected source${snapshot.selected_sources === 1 ? "" : "s"}`,
        `${snapshot.discovered_files} file${snapshot.discovered_files === 1 ? "" : "s"} discovered`,
        `${snapshot.supported_files} supported so far.`,
      ].join(" · ");
    case "importing": {
      const current = snapshot.current_source_name
        ? ` · current file: ${snapshot.current_source_name}`
        : "";
      return `Importing ${snapshot.processed_files} of ${snapshot.supported_files} supported file${snapshot.supported_files === 1 ? "" : "s"}${current}`;
    }
    case "pausing":
      return "Pausing the Import Batch after the current file finishes safely.";
    case "paused":
      return "Import Batch paused. Resume to continue importing.";
    default:
      return "";
  }
}

function skippedNote(summary: NonNullable<ImportTask["result"]>): string {
  return summary.unsupported_files > 0
    ? ` ${summary.unsupported_files} file(s) were skipped.`
    : "";
}

function failureDetailsNote(
  summary: NonNullable<ImportTask["result"]> | null,
): string {
  if (!summary || summary.failure_count === 0) return "";
  if (summary.failures_truncated) {
    return ` ${summary.failure_count} Import Failure(s) were recorded; the first ${summary.failure_details.length} are listed in the Library.`;
  }
  return ` ${summary.failure_count} Import Failure(s) are listed in the Library.`;
}

export function importResultMessage(snapshot: ImportTask): string {
  const summary = snapshot.result ?? snapshot.partial_result;
  switch (snapshot.status) {
    case "completed":
      return summary
        ? `Import Batch completed: ${importAssetTotals(summary)} were added to the Library.${skippedNote(summary)}`
        : "Import Batch completed.";
    case "completed_with_errors":
      return summary
        ? `Import Batch finished with errors: ${summary.succeeded_files} of ${summary.processed_files} file(s) imported, ${summary.failed_files} failed. Committed: ${importAssetTotals(summary)}.${skippedNote(summary)}${failureDetailsNote(summary)}`
        : "Import Batch finished with errors.";
    case "failed":
      return summary && importCommittedAssets(summary)
        ? `Import Batch failed early. Committed before stopping: ${importAssetTotals(summary)}.${failureDetailsNote(summary)}`
        : `Import Batch failed before any file was committed to the Library.${failureDetailsNote(summary)}`;
    case "cancelled":
      return summary && importCommittedAssets(summary)
        ? `The application stopped the Import Batch on shutdown. Committed Assets remain in the Library: ${importAssetTotals(summary)}.${failureDetailsNote(summary)} Make a fresh selection to import again after restart.`
        : "The application stopped the Import Batch on shutdown before any file was committed. Make a fresh selection to import again after restart.";
    default:
      return "";
  }
}
