import type { ImportTask } from "../../api/types";

export const IMPORT_TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
]);

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

function assetTotals(summary: NonNullable<ImportTask["result"]>): string {
  return `${summary.new_assets} new Asset${summary.new_assets === 1 ? "" : "(s)"} and ${summary.duplicate_assets} duplicate Asset${summary.duplicate_assets === 1 ? "" : "(s)"}`;
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

function committedAssets(summary: NonNullable<ImportTask["result"]>): boolean {
  return summary.new_assets + summary.duplicate_assets > 0;
}

function skippedNote(summary: NonNullable<ImportTask["result"]>): string {
  return summary.unsupported_files > 0
    ? ` ${summary.unsupported_files} file(s) were skipped.`
    : "";
}

export function importResultMessage(snapshot: ImportTask): string {
  const summary = snapshot.result ?? snapshot.partial_result;
  switch (snapshot.status) {
    case "completed":
      return summary
        ? `Import Batch completed: ${assetTotals(summary)} were added to the Library.${skippedNote(summary)}`
        : "Import Batch completed.";
    case "completed_with_errors":
      return summary
        ? `Import Batch finished with errors: ${summary.succeeded_files} of ${summary.processed_files} file(s) imported, ${summary.failed_files} failed. Committed: ${assetTotals(summary)}.${skippedNote(summary)}`
        : "Import Batch finished with errors.";
    case "failed":
      return summary && committedAssets(summary)
        ? `Import Batch failed early. Committed before stopping: ${assetTotals(summary)}.`
        : "Import Batch failed before any file was committed to the Library.";
    case "cancelled":
      return summary && committedAssets(summary)
        ? `The application stopped the Import Batch on shutdown. Committed Assets remain in the Library: ${assetTotals(summary)}. Make a fresh selection to import again after restart.`
        : "The application stopped the Import Batch on shutdown before any file was committed.";
    default:
      return "";
  }
}
