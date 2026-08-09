export interface AppState {
  library_root: string;
  runtime: {
    backend_name: string;
    device: string;
  };
  setup_state: {
    health_check_ok: boolean;
    runtime_readiness?: {
      ready: boolean;
      ready_detail?: string;
    };
  };
  library_status: {
    total_assets: number;
    job_counts: Record<string, number>;
  };
  worker_loop: {
    paused: boolean;
    running: boolean;
  };
  import_task: ImportTask;
  pending_jobs: Array<{ job_id: string }>;
}

export interface ImportTask {
  status: string;
  running: boolean;
  paused: boolean;
  pause_requested: boolean;
  source_folder: string | null;
  started_at: number | null;
  finished_at: number | null;
  result: {
    new_assets?: number;
    duplicate_assets?: number;
  } | null;
  error: {
    detail: string;
  } | null;
}

export interface FolderSelection {
  selected_path: string | null;
}

export interface SearchResult {
  library_root: string;
  active_recipe_id: string;
  active_recipe_label: string;
  query: string;
  top_k: number;
  results: SearchAsset[];
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
