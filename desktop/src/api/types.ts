export interface AppState {
  library_root: string;
  runtime: {
    backend_name: string;
    device: string;
    model_label?: string;
    output_dimension?: number;
    storage_dtype?: string;
  };
  setup_state: {
    health_check_ok: boolean;
    runtime_readiness?: {
      ready: boolean;
      ready_detail?: string;
    };
    checklist?: Array<{ id: string; label: string; done: boolean; detail: string }>;
  };
  library_status: {
    total_assets: number;
    job_counts: Record<string, number>;
    recent_jobs?: RecentJob[];
  };
  worker_loop: WorkerLoopState;
  import_task: ImportTask;
  pending_jobs: Array<{ job_id: string }>;
}

export interface WorkerLoopState {
  paused: boolean;
  running: boolean;
  interval_seconds?: number;
  last_tick_started_at?: number | null;
  last_tick_finished_at?: number | null;
  event_log_path?: string | null;
  recent_events?: WorkerLoopEvent[];
  persisted_events?: WorkerLoopEvent[];
}

export interface RecentJob {
  job_id: string;
  type: string;
  status: string;
  asset_id: string | null;
  recipe_id: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  error_detail: string | null;
}

export interface WorkerLoopEvent {
  event: string;
  payload: Record<string, unknown>;
  timestamp: number;
}

export interface RuntimeHealthDiagnosticStep {
  step: string;
  status: string;
  detail: string;
}

export interface RuntimeHealthResult {
  runtime_fingerprint: string;
  backend_name: string;
  device: string;
  gpu_name: string | null;
  gpu_vendor: string | null;
  gpu_vendor_id: string | null;
  text_smoke_vector_dim: number | null;
  image_smoke_vector_dim: number | null;
  diagnostic_steps: RuntimeHealthDiagnosticStep[];
  smoke_test_ok: boolean;
  error: string | null;
}

export interface PendingJob {
  job_id: string;
  type: string;
  asset_id: string | null;
  asset_path: string | null;
  recipe_id: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
}

export interface PendingJobsResult {
  jobs: PendingJob[];
}

export interface DeletePendingJobsResult {
  requested_job_ids: string[];
  deleted_job_ids: string[];
  skipped_job_ids: string[];
}

export interface RetryJobsResult {
  library_root: string;
  retried_jobs: number;
  failed_jobs_remaining: number;
}

export interface ImportFailureDetail {
  stage: string;
  code: string;
  source_name: string;
  detail: string;
}

export interface ImportBatchResultSummary {
  library_root: string;
  selected_sources: number;
  effective_sources: number;
  discovered_files: number;
  supported_files: number;
  unsupported_files: number;
  reparse_points_skipped: number;
  scan_failures: number;
  processed_files: number;
  succeeded_files: number;
  failed_files: number;
  new_assets: number;
  duplicate_assets: number;
  source_records_added: number;
  source_records_refreshed: number;
  jobs_created: number;
  failure_details: ImportFailureDetail[];
  active_recipe_id: string | null;
}

export type ImportBatchStatus =
  | "idle"
  | "scanning"
  | "importing"
  | "pausing"
  | "paused"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";

export interface ImportTask {
  batch_id: string | null;
  status: ImportBatchStatus;
  running: boolean;
  paused: boolean;
  pause_requested: boolean;
  source_folder: string | null;
  selected_sources: number;
  effective_sources: number;
  discovered_files: number;
  supported_files: number;
  unsupported_files: number;
  reparse_points_skipped: number;
  scan_failures: number;
  processed_files: number;
  succeeded_files: number;
  failed_files: number;
  new_assets: number;
  duplicate_assets: number;
  source_records_added: number;
  source_records_refreshed: number;
  jobs_created: number;
  current_source_name: string | null;
  started_at: number | null;
  finished_at: number | null;
  result: ImportBatchResultSummary | null;
  partial_result: ImportBatchResultSummary | null;
  error: { error: string; detail: string } | null;
}

export interface FolderSelection {
  selected_path: string | null;
}

export interface LibrarySelectionSummary {
  selection_id: string;
  count: number;
}

export interface SearchResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  query: string;
  top_k: number;
  results: SearchAsset[];
}

export interface ImageSearchResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  query_path: string;
  query_media_type: string;
  top_k: number;
  results: SearchAsset[];
}

export interface SimilarityResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  asset_id: string;
  top_k: number;
  results: SearchAsset[];
}

export interface DuplicateScanResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  threshold: number;
  pairs: DuplicatePair[];
}

export interface DuplicatePair {
  score: number;
  asset_a_id: string;
  asset_b_id: string;
  asset_a_path: string;
  asset_b_path: string;
  asset_a_thumbnail_url: string | null;
  asset_b_thumbnail_url: string | null;
  asset_a_matched_source_ref: string | null;
  asset_b_matched_source_ref: string | null;
}

export interface SearchAsset {
  asset_id: string;
  library_url: string;
  thumbnail_url: string | null;
  library_path: string;
  media_type: string;
  score: number;
  match_sources: string[];
  ocr_snippet?: string | null;
}

export interface AssetSummary {
  asset_id: string;
  library_path: string;
  library_url: string;
  thumbnail_url: string | null;
  media_type: string;
  content_hash: string;
  width: number | null;
  height: number | null;
  imported_at: string;
  updated_at: string;
  source_record_count: number;
  source_records: Array<{ source_path: string }>;
  status: "pending" | "indexed" | "failed" | string;
}

export interface AssetListResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  assets: AssetSummary[];
}

export interface AssetDetail extends AssetSummary {
  ocr_status: "ready" | "missing" | string;
  source_records: Array<{
    source_path: string;
    imported_at: string;
    last_seen_at: string | null;
  }>;
  indexed_recipe_labels: string[];
  stale_recipe_labels: string[];
  ocr_results: Array<{
    result_id: string;
    text: string;
    confidence: number | null;
    language_hint: string | null;
    created_at: string;
  }>;
  renditions: Array<{
    kind: string;
    path: string;
    url: string;
    width: number | null;
    height: number | null;
    frame_index: number | null;
    created_at: string;
  }>;
  jobs: Array<{
    job_id: string;
    type: string;
    status: string;
    recipe_id: string | null;
    attempt_count: number;
  }>;
}

export interface AssetDetailResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  asset: AssetDetail;
}

export interface AssetMutationResult {
  library_root: string;
  asset_id: string;
  removed_source_path: string | null;
  asset_deleted: boolean;
  removed_source_records: number;
  removed_jobs: number;
  removed_renditions: number;
  removed_embeddings: number;
}

export interface BatchAssetActionResult {
  library_root: string;
  action: "delete" | "rebuild-active-index";
  requested_asset_ids: string[];
  affected_asset_ids: string[];
  skipped_running_asset_ids: string[];
  removed_source_records: number;
  removed_jobs: number;
  removed_renditions: number;
  removed_embeddings: number;
  reindex_jobs_created: number;
}
