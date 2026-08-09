import { invoke } from "@tauri-apps/api/core";
import type { AppState, AssetDetailResult, AssetListResult } from "./types";

export type TauriInvoker = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export interface MemeSortClient {
  getAppState(): Promise<AppState>;
  getAssets(): Promise<AssetListResult>;
  getAssetDetail(assetId: string): Promise<AssetDetailResult>;
}

export function createMemeSortClient(invokeCommand: TauriInvoker): MemeSortClient {
  return {
    getAppState: () => invokeCommand<AppState>("get_app_state"),
    getAssets: () => invokeCommand<AssetListResult>("get_assets"),
    getAssetDetail: (assetId) => invokeCommand<AssetDetailResult>("get_asset_detail", { assetId }),
  };
}

export const tauriClient = createMemeSortClient(invoke);
