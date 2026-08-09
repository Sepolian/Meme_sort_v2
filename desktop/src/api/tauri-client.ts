import { invoke } from "@tauri-apps/api/core";
import type { AppState } from "./types";

export type TauriInvoker = <T>(command: string) => Promise<T>;

export interface MemeSortClient {
  getAppState(): Promise<AppState>;
}

export function createMemeSortClient(invokeCommand: TauriInvoker): MemeSortClient {
  return {
    getAppState: () => invokeCommand<AppState>("get_app_state"),
  };
}

export const tauriClient = createMemeSortClient(invoke);
