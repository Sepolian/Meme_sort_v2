import { useQuery } from "@tanstack/react-query";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import "./App.css";

interface AppProps {
  client?: MemeSortClient;
}

export function App({ client = tauriClient }: AppProps) {
  const stateQuery = useQuery({
    queryKey: ["app-state"],
    queryFn: () => client.getAppState(),
    refetchInterval: 5_000,
  });

  if (stateQuery.isPending) {
    return <main className="migration-status">Connecting to authenticated sidecar…</main>;
  }

  if (stateQuery.isError) {
    return (
      <main className="migration-status">
        Cannot reach the authenticated sidecar. Restart MemeSort to retry.
      </main>
    );
  }

  const state = stateQuery.data;
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;

  return (
    <main className="migration-status" aria-labelledby="page-title">
      <section className="status-card">
        <p className="eyebrow">MemeSort desktop</p>
        <h1 id="page-title">MemeSort is connected</h1>
        <p>
          The Tauri host is proxying the authenticated Python sidecar. The
          WebView cannot access its loopback API, bootstrap secret, or session
          cookie directly.
        </p>
        <dl>
          <div>
            <dt>Assets</dt>
            <dd>{state.library_status.total_assets}</dd>
          </div>
          <div>
            <dt>Runtime</dt>
            <dd>
              {state.runtime.backend_name} / {state.runtime.device}
            </dd>
          </div>
          <div>
            <dt>Queue</dt>
            <dd>{pendingJobs} pending job(s)</dd>
          </div>
          <div>
            <dt>Worker</dt>
            <dd>{state.worker_loop.paused ? "Paused" : "Running"}</dd>
          </div>
        </dl>
      </section>
    </main>
  );
}

export default App;
