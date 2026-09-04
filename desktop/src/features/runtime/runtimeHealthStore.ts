import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import type { RuntimeHealthResult } from "../../api/types";

export type RuntimeHealthStatus = "idle" | "checking" | "healthy" | "failed";

export interface RuntimeHealthSnapshot {
  status: RuntimeHealthStatus;
  /** Current-session result from `runRuntimeHealthCheck`, or null before completion. */
  result: RuntimeHealthResult | null;
  /** Human-readable failure detail when status is `failed`. */
  error: string | null;
  /** Whether the automatic startup check has been claimed for this session. */
  automaticStarted: boolean;
}

const initialSnapshot: RuntimeHealthSnapshot = {
  status: "idle",
  result: null,
  error: null,
  automaticStarted: false,
};

let snapshot: RuntimeHealthSnapshot = { ...initialSnapshot };
let listeners = new Set<() => void>();
let inFlight: Promise<RuntimeHealthSnapshot> | null = null;

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

function setSnapshot(next: RuntimeHealthSnapshot): void {
  snapshot = next;
  notify();
}

export function getRuntimeHealthSnapshot(): RuntimeHealthSnapshot {
  return snapshot;
}

export function subscribeRuntimeHealth(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Current-session authorization: only a healthy check in this session authorizes indexing/search. */
export function isRuntimeAuthorizedForSession(current: RuntimeHealthSnapshot): boolean {
  return current.status === "healthy" && (current.result?.smoke_test_ok ?? false);
}

/** Failure blocks semantic search/indexing while browsing/import stay enabled. */
export function isRuntimeBlockedForSemantic(current: RuntimeHealthSnapshot): boolean {
  return current.status === "failed";
}

function failureMessage(error: unknown, result: RuntimeHealthResult | null): string {
  if (result?.error) return result.error;
  try {
    return tauriErrorDetail(error, "MemeSort could not run the Vulkan health check.");
  } catch {
    return "MemeSort could not run the Vulkan health check.";
  }
}

async function runCheck(client: MemeSortClient): Promise<RuntimeHealthSnapshot> {
  setSnapshot({ status: "checking", result: snapshot.result, error: null, automaticStarted: snapshot.automaticStarted });
  try {
    const result = await client.runRuntimeHealthCheck();
    if (result.smoke_test_ok) {
      const next: RuntimeHealthSnapshot = {
        status: "healthy",
        result,
        error: null,
        automaticStarted: snapshot.automaticStarted,
      };
      setSnapshot(next);
      return next;
    }
    const next: RuntimeHealthSnapshot = {
      status: "failed",
      result,
      error: result.error ?? "Runtime health check failed.",
      automaticStarted: snapshot.automaticStarted,
    };
    setSnapshot(next);
    return next;
  } catch (error) {
    const next: RuntimeHealthSnapshot = {
      status: "failed",
      result: null,
      error: failureMessage(error, null),
      automaticStarted: snapshot.automaticStarted,
    };
    setSnapshot(next);
    return next;
  } finally {
    inFlight = null;
  }
}

/**
 * Application-scoped automatic start (ticket 14).
 *
 * Survives component and route remounts via module state. StrictMode double
 * effects, remounts, and polling share the same claim: at most one automatic
 * client invocation per session. Concurrent automatic callers share the
 * running promise instead of racing.
 */
export function ensureAutomaticRuntimeHealthCheck(
  client: MemeSortClient,
): Promise<RuntimeHealthSnapshot> {
  if (snapshot.automaticStarted) {
    if (inFlight) return inFlight;
    return Promise.resolve(snapshot);
  }
  // A non-idle snapshot means a check already ran or is running (e.g. an
  // explicit retry won the race). Claim automatic without a duplicate call.
  if (snapshot.status !== "idle") {
    setSnapshot({ ...snapshot, automaticStarted: true });
    if (inFlight) return inFlight;
    return Promise.resolve(snapshot);
  }
  setSnapshot({ ...snapshot, automaticStarted: true });
  inFlight = runCheck(client);
  return inFlight;
}

/**
 * Explicit user Retry (ticket 14).
 *
 * May start a later check after completion. While a check is running, Retry
 * shares the running result instead of starting a duplicate.
 */
export function retryRuntimeHealthCheck(client: MemeSortClient): Promise<RuntimeHealthSnapshot> {
  if (inFlight) return inFlight;
  // Mark automatic as claimed so a late automatic start does not duplicate
  // an explicit user request.
  if (!snapshot.automaticStarted) {
    setSnapshot({ ...snapshot, automaticStarted: true });
  }
  inFlight = runCheck(client);
  return inFlight;
}

/** Test-only reset for the module singleton between isolated cases. */
export function resetRuntimeHealthForTesting(): void {
  snapshot = { ...initialSnapshot };
  inFlight = null;
  notify();
}
