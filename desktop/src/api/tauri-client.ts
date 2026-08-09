import { invoke } from "@tauri-apps/api/core";
import type { AppState, AssetDetailResult, AssetListResult, AssetMutationResult, BatchAssetActionResult, FolderSelection, ImportTask } from "./types";

export type TauriInvoker = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export interface MemeSortClient {
  getAppState(): Promise<AppState>;
  getAssets(): Promise<AssetListResult>;
  getAssetDetail(assetId: string): Promise<AssetDetailResult>;
  deleteAsset(assetId: string): Promise<AssetMutationResult>;
  removeSourceRecord(assetId: string, sourcePath: string): Promise<AssetMutationResult>;
  batchAssetAction(action: "delete" | "rebuild-active-index", assetIds: string[]): Promise<BatchAssetActionResult>;
  chooseImportFolder(): Promise<FolderSelection>;
  startImport(): Promise<ImportTask>;
  startImportAndIndex(): Promise<ImportTask>;
  pauseImport(): Promise<ImportTask>;
  resumeImport(): Promise<ImportTask>;
}

export function createMemeSortClient(invokeCommand: TauriInvoker): MemeSortClient {
  return {
    getAppState: () => invokeCommand<AppState>("get_app_state"),
    getAssets: () => invokeCommand<AssetListResult>("get_assets"),
    getAssetDetail: (assetId) => invokeCommand<AssetDetailResult>("get_asset_detail", { assetId }),
    deleteAsset: (assetId) => invokeCommand<AssetMutationResult>("delete_asset", { assetId }),
    removeSourceRecord: (assetId, sourcePath) => invokeCommand<AssetMutationResult>("remove_source_record", { assetId, sourcePath }),
    batchAssetAction: (action, assetIds) => invokeCommand<BatchAssetActionResult>("batch_asset_action", { action, assetIds }),
    chooseImportFolder: () => invokeCommand<FolderSelection>("choose_import_folder"),
    startImport: () => invokeCommand<ImportTask>("start_import"),
    startImportAndIndex: () => invokeCommand<ImportTask>("start_import_and_index"),
    pauseImport: () => invokeCommand<ImportTask>("pause_import"),
    resumeImport: () => invokeCommand<ImportTask>("resume_import"),
  };
}

export const tauriClient = createMemeSortClient(invoke);
