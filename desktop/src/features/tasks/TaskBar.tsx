import { useState } from "react";
import { Link } from "react-router-dom";
import type { AppState } from "../../api/types";
import { useImportBatch } from "../import/ImportBatchContext";
import { useRuntimeHealth } from "../runtime/RuntimeHealthProvider";
import { summarizeTasks } from "./taskVisibility";

const MINIMIZED_STORAGE_KEY = "memesort.taskbar.minimized";

function readMinimized(): boolean {
  try {
    return window.localStorage.getItem(MINIMIZED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Minimizable bottom task bar for import, indexing, and Runtime health
 * (ticket 15).
 *
 * Visible only for active, failed, or otherwise actionable work. Returns null
 * for idle/non-actionable state so it automatically disappears. Minimized
 * state only collapses details; it never keeps an idle bar visible and never
 * hides attention-required work.
 */
export function TaskBar({ appState }: { appState: AppState | null }) {
  const batch = useImportBatch();
  const health = useRuntimeHealth();
  const [minimized, setMinimized] = useState<boolean>(() => readMinimized());

  const summary = summarizeTasks({
    importTask: batch.snapshot,
    healthStatus: health.status,
    healthBlocked: health.isBlocked,
    appState,
  });

  if (!summary.visible || !summary.compactLabel) return null;

  const setMinimizedPersisted = (next: boolean) => {
    setMinimized(next);
    try {
      window.localStorage.setItem(MINIMIZED_STORAGE_KEY, next ? "1" : "0");
    } catch {
      // Storage is a convenience only; the bar still toggles in memory.
    }
  };

  const details: Array<{ label: string; text: string }> = [];
  if (summary.importLabel) details.push({ label: "Import", text: summary.importLabel });
  if (summary.indexingLabel) details.push({ label: "Indexing", text: summary.indexingLabel });
  if (summary.healthLabel) details.push({ label: "Runtime health", text: summary.healthLabel });

  return (
    <section className="task-bar" role="region" aria-label="Background tasks" data-tone={summary.tone ?? "active"}>
      <div className="task-bar-header">
        <strong>{summary.compactLabel}</strong>
        <div className="task-bar-actions">
          <button
            className="button button-secondary task-bar-toggle"
            type="button"
            onClick={() => setMinimizedPersisted(!minimized)}
          >
            {minimized ? "Expand tasks" : "Minimize tasks"}
          </button>
          <Link className="text-button" to="/settings">
            Open diagnostics
          </Link>
        </div>
      </div>
      {!minimized ? (
        <ul className="detail-list task-bar-details">
          {details.map((detail) => (
            <li key={detail.label}>
              <strong>{detail.label}</strong>
              <span>{detail.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export default TaskBar;
