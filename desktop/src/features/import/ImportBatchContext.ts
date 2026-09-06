import { createContext, useContext } from "react";
import type { ImportTask } from "../../api/types";
import type { ImportBatchStartBlockedReason } from "./import-status";

export type { ImportBatchStartBlockedReason };

export type ImportBatchStartResult =
  | { kind: "started"; snapshot: ImportTask }
  | { kind: "blocked"; reason: ImportBatchStartBlockedReason };

export interface ImportBatchContextValue {
  snapshot: ImportTask | null;
  /** True while a launch from any entry point is still awaiting the backend. */
  starting: boolean;
  startBatch: (start: () => Promise<ImportTask>) => Promise<ImportBatchStartResult>;
  requestPause: () => Promise<void>;
  requestResume: () => Promise<void>;
  controlsPending: boolean;
}

export const ImportBatchContext = createContext<ImportBatchContextValue | null>(null);

export function useImportBatch(): ImportBatchContextValue {
  const context = useContext(ImportBatchContext);
  if (!context) {
    throw new Error("useImportBatch must be used within ImportBatchProvider.");
  }
  return context;
}
