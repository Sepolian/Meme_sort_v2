import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import type { AppState } from "../../api/types";

interface AdvancedDiagnosticsProps {
  client: MemeSortClient;
  appState: AppState;
  onStateChanged: () => void;
}

/**
 * Settings > Advanced Diagnostics (ticket 15).
 *
 * Parity destination for every legacy Status capability: Worker Loop
 * pause/resume/one tick, failed-Job retry, Pending Job inspect/select/delete,
 * Recent Jobs, in-memory and persisted Worker events, and opening the persisted
 * log directory. Uses only typed `MemeSortClient` methods and keeps the
 * explicit Pending Job selection + confirmation behavior (deletes queue records
 * only, never Assets).
 */
export function AdvancedDiagnostics({ client, appState, onStateChanged }: AdvancedDiagnosticsProps) {
  const [isWorking, setIsWorking] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [selectedPendingJobIds, setSelectedPendingJobIds] = useState<Set<string>>(() => new Set());
  const [pendingJobDeleteConfirmation, setPendingJobDeleteConfirmation] = useState(false);
  const pendingJobsQuery = useQuery({ queryKey: ["pending-jobs"], queryFn: () => client.getPendingJobs() });

  const runWorkerAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setIsWorking(true);
    setFeedback(null);
    try {
      await action();
      setFeedback(successMessage);
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not update the Worker Loop."));
    } finally {
      setIsWorking(false);
    }
  };

  const retryFailedJobs = async () => {
    setIsWorking(true);
    setFeedback(null);
    try {
      const result = await client.retryFailedJobs();
      setFeedback(`Retried ${result.retried_jobs} failed Job record(s); ${result.failed_jobs_remaining} remain failed.`);
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not retry failed Job records. Assets and generated files were not modified."));
    } finally {
      setIsWorking(false);
    }
  };

  const togglePendingJob = (jobId: string) => {
    setSelectedPendingJobIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const deleteSelectedPendingJobs = async () => {
    const jobIds = [...selectedPendingJobIds];
    if (!jobIds.length) return;

    setIsWorking(true);
    setFeedback(null);
    try {
      const result = await client.deletePendingJobs(jobIds);
      setFeedback(`Deleted ${result.deleted_job_ids.length} Pending Job record(s); skipped ${result.skipped_job_ids.length}.`);
      setSelectedPendingJobIds(new Set());
      setPendingJobDeleteConfirmation(false);
      await pendingJobsQuery.refetch();
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not delete the selected Pending Job records. Assets and generated files were not modified."));
      setPendingJobDeleteConfirmation(false);
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <>
      <section className="surface import-card" aria-labelledby="diagnostics-worker-loop-title">
        <h2 id="diagnostics-worker-loop-title">Worker Loop</h2>
        <p>Controls apply only to the background indexing loop. They do not cancel a running semantic inference call.</p>
        <div className="import-actions">
          <button className="button" type="button" disabled={isWorking || !appState.worker_loop.paused} onClick={() => void runWorkerAction(client.resumeWorkerLoop, "Worker Loop resumed.")}>Resume worker</button>
          <button className="button button-secondary" type="button" disabled={isWorking || appState.worker_loop.paused} onClick={() => void runWorkerAction(client.pauseWorkerLoop, "Worker Loop paused.")}>Pause worker</button>
          <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => void retryFailedJobs()}>Retry failed Jobs</button>
          <button className="button button-secondary" type="button" disabled={isWorking || !appState.worker_loop.running} onClick={() => void runWorkerAction(client.triggerWorkerLoop, "Worker Loop tick requested.")}>Run one tick</button>
        </div>
        <p role="status">{feedback ?? (appState.worker_loop.paused ? "Worker Loop is paused." : "Worker Loop is running.")}</p>
      </section>
      <section className="surface import-card" aria-labelledby="diagnostics-pending-jobs-title">
        <h2 id="diagnostics-pending-jobs-title">Pending Jobs</h2>
        <p>Delete only unclaimed queue records. Assets and generated files remain unchanged.</p>
        {pendingJobsQuery.isPending ? <p aria-live="polite">Loading Pending Jobs…</p> : null}
        {pendingJobsQuery.isError ? <section className="notice notice-warning" role="alert"><strong>Pending Jobs are unavailable</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void pendingJobsQuery.refetch()}>Retry Pending Jobs</button></section> : null}
        {pendingJobsQuery.data ? (
          pendingJobsQuery.data.jobs.length ? <>
            <ul className="detail-list pending-job-list">
              {pendingJobsQuery.data.jobs.map((job) => <li key={job.job_id}>
                <label className="asset-select"><input type="checkbox" checked={selectedPendingJobIds.has(job.job_id)} onChange={() => togglePendingJob(job.job_id)} />Select Pending Job {job.type}</label>
                <span>{job.asset_path ?? "No Asset path"}{job.recipe_id ? ` · ${job.recipe_id}` : ""} · attempt {job.attempt_count}</span>
              </li>)}
            </ul>
            <div className="import-actions"><button className="button button-danger" type="button" disabled={isWorking || !selectedPendingJobIds.size} onClick={() => setPendingJobDeleteConfirmation(true)}>Delete selected Pending Jobs</button></div>
          </> : <p>No Pending Jobs are waiting to be claimed.</p>
        ) : null}
      </section>
      {pendingJobDeleteConfirmation ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={isWorking ? undefined : () => setPendingJobDeleteConfirmation(false)}>
          <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="diagnostics-pending-job-delete-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">Confirm change</p>
            <h2 id="diagnostics-pending-job-delete-title">Delete {selectedPendingJobIds.size} Pending Job(s)?</h2>
            <p>This deletes only unclaimed queue records. Assets and generated files remain unchanged.</p>
            <div className="dialog-actions"><button className="button button-secondary" type="button" disabled={isWorking} onClick={() => setPendingJobDeleteConfirmation(false)}>Cancel</button><button className="button button-danger" type="button" autoFocus disabled={isWorking} onClick={() => void deleteSelectedPendingJobs()}>{isWorking ? "Working…" : "Delete Pending Jobs"}</button></div>
          </section>
        </div>
      ) : null}
      <section className="surface import-card" aria-labelledby="diagnostics-recent-jobs-title">
        <h2 id="diagnostics-recent-jobs-title">Recent Jobs</h2>
        <p>Latest queue records for diagnosing retries and failures.</p>
        {(appState.library_status.recent_jobs?.length ?? 0) ? <ul className="detail-list">{appState.library_status.recent_jobs!.map((job) => <li key={job.job_id}><strong>{job.type} · {job.status} · attempt {job.attempt_count}</strong><span>{job.asset_id ?? "no Asset"} · updated {job.updated_at}</span>{job.error_detail ? <span>{job.error_detail}</span> : null}</li>)}</ul> : <p>No Job records are available yet.</p>}
      </section>
      <section className="surface import-card" aria-labelledby="diagnostics-worker-events-title">
        <h2 id="diagnostics-worker-events-title">Worker events</h2>
        <p>In-memory Worker Loop events from this application session.</p>
        {(appState.worker_loop.recent_events?.length ?? 0) ? <ul className="detail-list">{appState.worker_loop.recent_events!.map((event, index) => <li key={`${event.event}-${event.timestamp}-${index}`}><strong>{event.event}</strong><span>timestamp {event.timestamp}</span><code>{JSON.stringify(event.payload)}</code></li>)}</ul> : <p>No Worker Loop events yet.</p>}
      </section>
      <section className="surface import-card" aria-labelledby="diagnostics-persisted-worker-log-title">
        <h2 id="diagnostics-persisted-worker-log-title">Persisted worker log</h2>
        <p>Recent Worker Loop events written to the Library log.</p>
        <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => void runWorkerAction(client.openLogDirectory, "Opened Library log folder.")}>Open log folder</button>
        {appState.worker_loop.event_log_path ? <p className="mono">{appState.worker_loop.event_log_path}</p> : null}
        {(appState.worker_loop.persisted_events?.length ?? 0) ? <ul className="detail-list">{appState.worker_loop.persisted_events!.map((event, index) => <li key={`${event.event}-${event.timestamp}-${index}`}><strong>{event.event}</strong><span>timestamp {event.timestamp}</span><code>{JSON.stringify(event.payload)}</code></li>)}</ul> : <p>No persisted Worker events yet.</p>}
      </section>
    </>
  );
}

export default AdvancedDiagnostics;
