import { useQuery, type QueryObserverResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import type { RuntimeHealthResult } from "../../api/types";
import { RuntimeHealthContext, type RuntimeHealthContextValue, type RuntimeHealthSnapshot } from "./RuntimeHealthContext";

interface RuntimeHealthProviderProps {
  client: MemeSortClient;
  children: ReactNode;
}

function snapshotFromQuery(query: QueryObserverResult<RuntimeHealthResult>): RuntimeHealthSnapshot {
  if (query.isFetching) return { status: "checking", result: query.data ?? null, error: null };
  if (query.isError) {
    let error = "MemeSort could not run the Vulkan health check.";
    try {
      error = tauriErrorDetail(query.error, error);
    } catch {
      // Keep the fallback when an invalid thrown value cannot be formatted.
    }
    return { status: "failed", result: null, error };
  }
  if (!query.data) return { status: "idle", result: null, error: null };
  return query.data.smoke_test_ok
    ? { status: "healthy", result: query.data, error: null }
    : { status: "failed", result: query.data, error: query.data.error ?? "Runtime health check failed." };
}

/** The QueryClient owns one automatic check for the application session. */
export function RuntimeHealthProvider({ client, children }: RuntimeHealthProviderProps) {
  const query = useQuery({
    queryKey: ["runtime-health"],
    queryFn: () => client.runRuntimeHealthCheck(),
    networkMode: "always",
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    retryOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const snapshot = snapshotFromQuery(query);
  const value: RuntimeHealthContextValue = {
    ...snapshot,
    isAuthorized: snapshot.status === "healthy" && !!snapshot.result?.smoke_test_ok,
    isBlocked: snapshot.status === "failed",
    retry: async () => snapshotFromQuery(await query.refetch({ cancelRefetch: false })),
  };
  return <RuntimeHealthContext.Provider value={value}>{children}</RuntimeHealthContext.Provider>;
}
