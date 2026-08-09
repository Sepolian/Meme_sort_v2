import { invoke } from "@tauri-apps/api/core";
import type { AppState, AssetDetailResult, AssetListResult, AssetMutationResult, BatchAssetActionResult, DeletePendingJobsResult, DuplicateScanResult, FolderSelection, ImageSearchResult, ImportTask, PendingJobsResult, RetryJobsResult, RuntimeHealthResult, SearchResult, SimilarityResult, WorkerLoopState } from "./types";

export type TauriInvoker = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export interface MemeSortClient {
  getAppState(): Promise<AppState>;
  getAssets(): Promise<AssetListResult>;
  getAssetDetail(assetId: string): Promise<AssetDetailResult>;
  revealAsset(assetId: string, target: "managed" | "source", sourcePath?: string): Promise<void>;
  openLogDirectory(): Promise<void>;
  deleteAsset(assetId: string): Promise<AssetMutationResult>;
  removeSourceRecord(assetId: string, sourcePath: string): Promise<AssetMutationResult>;
  batchAssetAction(action: "delete" | "rebuild-active-index", assetIds: string[]): Promise<BatchAssetActionResult>;
  chooseImportFolder(): Promise<FolderSelection>;
  chooseSearchImage(): Promise<FolderSelection>;
  startImport(): Promise<ImportTask>;
  startImportAndIndex(): Promise<ImportTask>;
  pauseImport(): Promise<ImportTask>;
  resumeImport(): Promise<ImportTask>;
  searchText(query: string, requestId: string): Promise<SearchResult>;
  searchImage(requestId: string): Promise<ImageSearchResult>;
  findSimilar(assetId: string): Promise<SimilarityResult>;
  getDuplicates(threshold: number): Promise<DuplicateScanResult>;
  pauseWorkerLoop(): Promise<WorkerLoopState>;
  resumeWorkerLoop(): Promise<WorkerLoopState>;
  triggerWorkerLoop(): Promise<WorkerLoopState>;
  runRuntimeHealthCheck(): Promise<RuntimeHealthResult>;
  retryFailedJobs(): Promise<RetryJobsResult>;
  getPendingJobs(): Promise<PendingJobsResult>;
  deletePendingJobs(jobIds: string[]): Promise<DeletePendingJobsResult>;
  cancelSearch(requestId: string): Promise<{ request_id: string; cancelled: boolean; was_active: boolean }>;
}

export function createMemeSortClient(invokeCommand: TauriInvoker): MemeSortClient {
  return {
    getAppState: () => invokeCommand<AppState>("get_app_state"),
    getAssets: () => invokeCommand<AssetListResult>("get_assets"),
    getAssetDetail: (assetId) => invokeCommand<AssetDetailResult>("get_asset_detail", { assetId }),
    revealAsset: (assetId, target, sourcePath) => invokeCommand("reveal_asset", sourcePath === undefined ? { assetId, target } : { assetId, target, sourcePath }),
    openLogDirectory: () => invokeCommand("open_log_directory"),
    deleteAsset: (assetId) => invokeCommand<AssetMutationResult>("delete_asset", { assetId }),
    removeSourceRecord: (assetId, sourcePath) => invokeCommand<AssetMutationResult>("remove_source_record", { assetId, sourcePath }),
    batchAssetAction: (action, assetIds) => invokeCommand<BatchAssetActionResult>("batch_asset_action", { action, assetIds }),
    chooseImportFolder: () => invokeCommand<FolderSelection>("choose_import_folder"),
    chooseSearchImage: () => invokeCommand<FolderSelection>("choose_search_image"),
    startImport: () => invokeCommand<ImportTask>("start_import"),
    startImportAndIndex: () => invokeCommand<ImportTask>("start_import_and_index"),
    pauseImport: () => invokeCommand<ImportTask>("pause_import"),
    resumeImport: () => invokeCommand<ImportTask>("resume_import"),
    searchText: (query, requestId) => invokeCommand<SearchResult>("search_text", { query, requestId }),
    searchImage: (requestId) => invokeCommand<ImageSearchResult>("search_image", { requestId }),
    findSimilar: (assetId) => invokeCommand<SimilarityResult>("find_similar", { assetId }),
    getDuplicates: (threshold) => invokeCommand<DuplicateScanResult>("get_duplicates", { threshold }),
    pauseWorkerLoop: () => invokeCommand<WorkerLoopState>("pause_worker_loop"),
    resumeWorkerLoop: () => invokeCommand<WorkerLoopState>("resume_worker_loop"),
    triggerWorkerLoop: () => invokeCommand<WorkerLoopState>("trigger_worker_loop"),
    runRuntimeHealthCheck: () => invokeCommand<RuntimeHealthResult>("run_runtime_health_check"),
    retryFailedJobs: () => invokeCommand<RetryJobsResult>("retry_failed_jobs"),
    getPendingJobs: () => invokeCommand<PendingJobsResult>("get_pending_jobs"),
    deletePendingJobs: (jobIds) => invokeCommand<DeletePendingJobsResult>("delete_pending_jobs", { jobIds }),
    cancelSearch: (requestId) => invokeCommand("cancel_search", { requestId }),
  };
}

export const tauriClient = createMemeSortClient(invoke);
