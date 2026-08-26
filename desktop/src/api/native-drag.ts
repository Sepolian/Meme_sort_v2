import { listen } from "@tauri-apps/api/event";

export const NATIVE_DRAG_EVENT = "library-native-drag";

export interface NativeDragSummary {
  phase: "enter" | "over" | "leave" | "drop";
  fileCount: number;
  folderCount: number;
  x: number;
  y: number;
  accepted: boolean;
  dropId: string | null;
}

export type NativeDragListener = (summary: NativeDragSummary) => void;
export type NativeDragSubscribe = (listener: NativeDragListener) => () => void;

interface NativeDragSummaryPayload {
  phase?: unknown;
  file_count?: unknown;
  folder_count?: unknown;
  x?: unknown;
  y?: unknown;
  accepted?: unknown;
  drop_id?: unknown;
}

const phases = ["enter", "over", "leave", "drop"] as const;
type NativeDragPhase = (typeof phases)[number];

function toSummary(payload: NativeDragSummaryPayload): NativeDragSummary | null {
  if (
    typeof payload?.phase !== "string"
    || !phases.includes(payload.phase as NativeDragPhase)
  ) {
    return null;
  }
  const numberOr = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);
  return {
    phase: payload.phase as NativeDragPhase,
    fileCount: numberOr(payload.file_count),
    folderCount: numberOr(payload.folder_count),
    x: numberOr(payload.x),
    y: numberOr(payload.y),
    accepted: payload.accepted === true,
    dropId: typeof payload.drop_id === "string" ? payload.drop_id : null,
  };
}

/**
 * Subscribes to the desktop host's path-free native drag summaries.
 * Returns a synchronous disposer so route changes can clean up reliably.
 */
export function subscribeNativeDrag(listener: NativeDragListener): () => void {
  let unlisten: (() => void) | null = null;
  let disposed = false;
  void listen<NativeDragSummaryPayload>(NATIVE_DRAG_EVENT, (event) => {
    const summary = toSummary(event.payload);
    if (summary) listener(summary);
  }).then(
    (dispose) => {
      if (disposed) dispose();
      else unlisten = dispose;
    },
    () => undefined,
  );
  return () => {
    disposed = true;
    unlisten?.();
  };
}
