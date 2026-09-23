import { createContext } from "react";
import type { RuntimeHealthResult } from "../../api/types";

export interface RuntimeHealthSnapshot {
  status: "idle" | "checking" | "healthy" | "failed";
  result: RuntimeHealthResult | null;
  error: string | null;
}

export interface RuntimeHealthContextValue extends RuntimeHealthSnapshot {
  isAuthorized: boolean;
  isBlocked: boolean;
  retry: () => Promise<RuntimeHealthSnapshot>;
}

export const RuntimeHealthContext = createContext<RuntimeHealthContextValue | null>(null);
