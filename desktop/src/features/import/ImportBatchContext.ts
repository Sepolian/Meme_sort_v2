import { createContext, useContext } from "react";
import type { ImportTask } from "../../api/types";

export interface ImportBatchContextValue {
  snapshot: ImportTask | null;
  requestPause: () => Promise<void>;
  requestResume: () => Promise<void>;
  controlsPending: boolean;
}

export const ImportBatchContext = createContext<ImportBatchContextValue | null>(null);

export function useImportBatch(): ImportBatchContextValue | null {
  return useContext(ImportBatchContext);
}
