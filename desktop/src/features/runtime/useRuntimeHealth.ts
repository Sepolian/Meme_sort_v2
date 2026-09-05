import { useContext } from "react";
import { RuntimeHealthContext, type RuntimeHealthContextValue } from "./RuntimeHealthContext";

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
