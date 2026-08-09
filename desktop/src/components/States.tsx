import type { ReactNode } from "react";

export function LoadingState() {
  return (
    <main className="state-panel" aria-live="polite">
      <p className="eyebrow">MemeSort desktop</p>
      <h1>Connecting to the authenticated sidecar</h1>
      <p>Loading Library and runtime status.</p>
    </main>
  );
}

export function SidecarDisconnected({ onRetry }: { onRetry: () => void }) {
  return (
    <main className="state-panel state-error" aria-live="assertive">
      <p className="eyebrow">Connection unavailable</p>
      <h1>MemeSort cannot reach its sidecar</h1>
      <p>The Library is unchanged. Retry the connection or restart MemeSort if the sidecar did not start.</p>
      <button className="button" type="button" onClick={onRetry}>Retry connection</button>
    </main>
  );
}

export function RuntimeNotReady({ detail }: { detail?: string }) {
  return (
    <section className="notice notice-warning" role="status">
      <strong>Runtime not ready</strong>
      <span>{detail ?? "Install the pinned runtime and run a Vulkan health check before indexing."}</span>
    </section>
  );
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return (
    <section className="empty-state">
      <h2>{title}</h2>
      <p>{detail}</p>
      {action ? <div>{action}</div> : null}
    </section>
  );
}
