import type { AppState } from "../../api/types";
import { useImportBatch } from "../import/ImportBatchContext";
import { useRuntimeHealth } from "../runtime/RuntimeHealthProvider";
import { summarizeTasks } from "./taskVisibility";

/**
 * Compact top-bar task entry (ticket 15).
 *
 * Read-only status derived from the same summary as the bottom task bar, so
 * both surfaces agree. Action controls live only in the expanded task bar and
 * in Settings > Advanced Diagnostics; this entry never duplicates them.
 */
export function TopBarTaskEntry({ appState }: { appState: AppState | null }) {
  const batch = useImportBatch();
  const health = useRuntimeHealth();
  const summary = summarizeTasks({
    importTask: batch.snapshot,
    healthStatus: health.status,
    healthBlocked: health.isBlocked,
    appState,
  });

  if (!summary.visible || !summary.compactLabel) return null;

  return (
    <span
      role="status"
      aria-label="Background tasks summary"
      className="task-top-entry"
      data-tone={summary.tone ?? "active"}
    >
      {summary.compactLabel}
    </span>
  );
}

export default TopBarTaskEntry;
