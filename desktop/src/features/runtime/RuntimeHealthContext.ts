import { createContext } from "react";
import type { RuntimeHealthSnapshot } from "./runtimeHealthStore";

export interface RuntimeHealthContextValue extends RuntimeHealthSnapshot {
  /** Current-session authorization for semantic search/indexing. */
  isAuthorized: boolean;
  /** Failure blocks semantic search/indexing; browsing/import remain enabled. */
  isBlocked: boolean;
  retry: () => Promise<RuntimeHealthSnapshot>;
}

export const RuntimeHealthContext = createContext<RuntimeHealthContextValue | null>(null);
