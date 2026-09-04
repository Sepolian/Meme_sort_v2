import { useState } from "react";
import { tauriErrorDetail } from "../../api/tauri-error";
import { useRuntimeHealth } from "./RuntimeHealthProvider";

/**
 * Compact Runtime health surfaces (ticket 14).
 *
 * - checking: compact "Preparing search" indicator (unobtrusive, no overlay).
 * - failed: blocks semantic search/indexing with an explanation, keeps
 *   browsing/import enabled, points to the external setup script (no in-app
 *   installer), and offers explicit Retry.
 * - healthy/idle: renders nothing to stay unobtrusive.
 */
export function RuntimeHealthCompactIndicator() {
  const health = useRuntimeHealth();
  if (health.status !== "checking") return null;
  return (
    <span role="status" aria-label="Runtime health" className="runtime-health-preparing">
      Preparing search…
    </span>
  );
}

/**
 * Semantic-action gate notice (ticket 14).
 *
 * Rendered inside search/indexing surfaces when the current session is not
 * authorized. Browsing/import remain enabled; only semantic actions block.
 */
export function SemanticUnavailableNotice() {
  const health = useRuntimeHealth();
  const [isRetrying, setIsRetrying] = useState(false);

  if (!health.isBlocked) return null;

  return (
    <section className="notice notice-warning" role="alert" aria-label="Semantic search unavailable">
      <strong>Semantic search is unavailable until the current session passes the Runtime health check.</strong>
      <span>{health.result?.error ?? health.error ?? "Runtime health check failed."}</span>
      <span>Library browsing and import still work. Run the external setup script to install the pinned runtime.</span>
      <div className="import-actions">
        <button
          className="button button-secondary"
          type="button"
          disabled={isRetrying}
          onClick={() => {
            setIsRetrying(true);
            void health.retry().finally(() => setIsRetrying(false));
          }}
        >
          {isRetrying ? "Retrying…" : "Retry health check"}
        </button>
      </div>
    </section>
  );
}

export function RuntimeHealthBanner() {
  const health = useRuntimeHealth();
  const [isRetrying, setIsRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  // Checking/idle owns the compact top-bar indicator only (ticket 14). The
  // banner stays null while checking to keep startup unobtrusive and avoid
  // duplicate status announcements.
  if (health.status === "checking" || health.status === "idle" || health.status === "healthy") return null;

  const detail = health.result?.error ?? health.error ?? "Runtime health check failed.";
  const steps = health.result?.diagnostic_steps ?? [];

  const onRetry = async () => {
    setIsRetrying(true);
    setRetryError(null);
    try {
      await health.retry();
    } catch (error) {
      setRetryError(tauriErrorDetail(error, "MemeSort could not retry the health check."));
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <section className="notice notice-warning" role="alert" aria-label="Runtime health failure">
      <strong>Semantic search and indexing are unavailable</strong>
      <span>{detail}</span>
      <span>Library browsing and import still work. Run the external setup script to install the pinned runtime; this app does not install the Runtime.</span>
      {steps.length ? (
        <ul className="detail-list">
          {steps.map((step) => (
            <li key={`${step.step}-${step.status}`}>
              <strong>
                {step.step} · {step.status} · {step.detail}
              </strong>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="import-actions">
        <button className="button button-secondary" type="button" disabled={isRetrying} onClick={() => void onRetry()}>
          {isRetrying ? "Retrying…" : "Retry health check"}
        </button>
      </div>
      {retryError ? <span>{retryError}</span> : null}
    </section>
  );
}
