import { createContext, useContext } from "react";
import type { ImportTask } from "../../api/types";

export interface ImportBatchContextValue {
  snapshot: ImportTask | null;
  startBatch: (start: () => Promise<ImportTask>) => Promise<ImportTask>;
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
