import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import type { ImportTask } from "../../api/types";
import {
  IMPORT_TERMINAL_STATUSES,
  importWorkIsActive,
} from "./import-status";
import { ImportBatchContext, type ImportBatchStartResult } from "./ImportBatchContext";

const ACTIVE_POLL_INTERVAL_MS = 1_000;
const IDLE_POLL_INTERVAL_MS = 5_000;

interface ImportBatchProviderProps {
  client: MemeSortClient;
  children: ReactNode;
}

export function ImportBatchProvider({ client, children }: ImportBatchProviderProps) {
  const queryClient = useQueryClient();
  const settledBatchIdRef = useRef<string | null>(null);
  const startingRef = useRef(false);
  const [starting, setStarting] = useState(false);

  const statusQuery = useQuery({
    queryKey: ["import-batch"],
    queryFn: () => client.getImportStatus(),
    refetchInterval: (query) =>
      importWorkIsActive(query.state.data ?? null)
        ? ACTIVE_POLL_INTERVAL_MS
        : IDLE_POLL_INTERVAL_MS,
  });
  // Launch coordination shared by every entry point (Import menu, native
  // drop). The guard is synchronous on purpose: a button disabled after the
  // next render is too late for two entry points acting in one event cycle.
  const startBatch = useCallback(
    async (start: () => Promise<ImportTask>): Promise<ImportBatchStartResult> => {
      if (startingRef.current) return { kind: "blocked", reason: "start-in-progress" };
      // Local knowledge only. When the snapshot is unknown or stale the launch
      // still goes to the backend, which remains the conflict authority.
      const known = queryClient.getQueryData<ImportTask | null>(["import-batch"]) ?? null;
      if (importWorkIsActive(known)) return { kind: "blocked", reason: "import-batch-active" };
      startingRef.current = true;
      setStarting(true);
      try {
        const snapshot = await start();
        // The confirmed snapshot lands immediately so the UI reflects the new
        // batch before any refresh; the shared launch refresh follows here so
        // entry points never repeat it.
        queryClient.setQueryData(["import-batch"], snapshot);
        try {
          await queryClient.invalidateQueries({ queryKey: ["app-state"] });
        } catch {
          // A failed refresh leaves App State stale for the next poll; it is
          // not a launch failure and must not invite a second import.
        }
        return { kind: "started", snapshot };
      } finally {
        startingRef.current = false;
        setStarting(false);
      }
    },
    [queryClient],
  );

  useEffect(() => {
    const snapshot = statusQuery.data;
    if (!snapshot) return;
    if (
      snapshot.running
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
      starting,
      startBatch,
      requestPause: async () => {
        await pauseMutation.mutateAsync();
      },
      requestResume: async () => {
        await resumeMutation.mutateAsync();
      },
      controlsPending: pauseMutation.isPending || resumeMutation.isPending,
    }),
    [statusQuery.data, starting, startBatch, pauseMutation, resumeMutation],
  );

  return (
    <ImportBatchContext.Provider value={value}>{children}</ImportBatchContext.Provider>
  );
}
