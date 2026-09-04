import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import {
  ensureAutomaticRuntimeHealthCheck,
  getRuntimeHealthSnapshot,
  isRuntimeAuthorizedForSession,
  isRuntimeBlockedForSemantic,
  retryRuntimeHealthCheck,
  subscribeRuntimeHealth,
  type RuntimeHealthSnapshot,
} from "./runtimeHealthStore";

export interface RuntimeHealthContextValue extends RuntimeHealthSnapshot {
  /** Current-session authorization for semantic search/indexing. */
  isAuthorized: boolean;
  /** Failure blocks semantic search/indexing; browsing/import remain enabled. */
  isBlocked: boolean;
  retry: () => Promise<RuntimeHealthSnapshot>;
}

const RuntimeHealthContext = createContext<RuntimeHealthContextValue | null>(null);

interface RuntimeHealthProviderProps {
  client: MemeSortClient;
  children: ReactNode;
}

/**
 * Application-scoped Runtime health controller (ticket 14).
 *
 * Owns the single automatic `runRuntimeHealthCheck` for the session. The
 * module store survives component and route remounts, so StrictMode double
 * effects and polling cannot start duplicates.
 */
export function RuntimeHealthProvider({ client, children }: RuntimeHealthProviderProps) {
  const snapshot = useSyncExternalStore(subscribeRuntimeHealth, getRuntimeHealthSnapshot);

  useEffect(() => {
    void ensureAutomaticRuntimeHealthCheck(client);
  }, [client]);

  const retry = useCallback(() => retryRuntimeHealthCheck(client), [client]);

  const value = useMemo<RuntimeHealthContextValue>(
    () => ({
      ...snapshot,
      isAuthorized: isRuntimeAuthorizedForSession(snapshot),
      isBlocked: isRuntimeBlockedForSemantic(snapshot),
      retry,
    }),
    [snapshot, retry],
  );

  return <RuntimeHealthContext.Provider value={value}>{children}</RuntimeHealthContext.Provider>;
}

export function useRuntimeHealth(): RuntimeHealthContextValue {
  const context = useContext(RuntimeHealthContext);
  if (!context) {
    throw new Error("useRuntimeHealth must be used within RuntimeHealthProvider.");
  }
  return context;
}

/** Optional access for isolated surfaces (e.g. unit-rendered workspaces). */
export function useOptionalRuntimeHealth(): RuntimeHealthContextValue | null {
  return useContext(RuntimeHealthContext);
}
