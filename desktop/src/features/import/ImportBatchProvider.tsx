import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import {
  IMPORT_TERMINAL_STATUSES,
  importWorkIsActive,
} from "./import-status";
import { ImportBatchContext } from "./ImportBatchContext";

const ACTIVE_POLL_INTERVAL_MS = 1_000;
const IDLE_POLL_INTERVAL_MS = 5_000;

interface ImportBatchProviderProps {
  client: MemeSortClient;
  children: ReactNode;
}

export function ImportBatchProvider({ client, children }: ImportBatchProviderProps) {
  const queryClient = useQueryClient();
  const wasRunningRef = useRef(false);
  const settledBatchIdRef = useRef<string | null>(null);

  const statusQuery = useQuery({
    queryKey: ["import-batch"],
    queryFn: () => client.getImportStatus(),
    refetchInterval: (query) =>
      importWorkIsActive(query.state.data ?? null)
        ? ACTIVE_POLL_INTERVAL_MS
        : IDLE_POLL_INTERVAL_MS,
  });

  useEffect(() => {
    const snapshot = statusQuery.data;
    if (!snapshot) return;
    const previouslyRunning = wasRunningRef.current;
    wasRunningRef.current = snapshot.running;
    if (
      !previouslyRunning
      || snapshot.running
      || !IMPORT_TERMINAL_STATUSES.has(snapshot.status)
      || snapshot.batch_id === null
    ) {
      return;
    }
    if (settledBatchIdRef.current === snapshot.batch_id) return;
    settledBatchIdRef.current = snapshot.batch_id;
    void queryClient.invalidateQueries({ queryKey: ["assets"] });
    void queryClient.invalidateQueries({ queryKey: ["app-state"] });
    void queryClient.invalidateQueries({ queryKey: ["asset-detail"] });
  }, [statusQuery.data, queryClient]);

  const pauseMutation = useMutation({
    mutationFn: () => client.pauseImport(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["import-batch"] }),
  });
  const resumeMutation = useMutation({
    mutationFn: () => client.resumeImport(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["import-batch"] }),
  });

  const value = useMemo(
    () => ({
      snapshot: statusQuery.data ?? null,
      requestPause: async () => {
        await pauseMutation.mutateAsync();
      },
      requestResume: async () => {
        await resumeMutation.mutateAsync();
      },
      controlsPending: pauseMutation.isPending || resumeMutation.isPending,
    }),
    [statusQuery.data, pauseMutation, resumeMutation],
  );

  return (
    <ImportBatchContext.Provider value={value}>{children}</ImportBatchContext.Provider>
  );
}
