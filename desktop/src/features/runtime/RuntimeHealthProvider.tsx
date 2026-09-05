import { useCallback, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import { RuntimeHealthContext, type RuntimeHealthContextValue } from "./RuntimeHealthContext";
import {
  ensureAutomaticRuntimeHealthCheck,
  getRuntimeHealthSnapshot,
  isRuntimeAuthorizedForSession,
  isRuntimeBlockedForSemantic,
  retryRuntimeHealthCheck,
  subscribeRuntimeHealth,
} from "./runtimeHealthStore";

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
