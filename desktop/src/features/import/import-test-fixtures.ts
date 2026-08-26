import type { ImportBatchResultSummary, ImportTask } from "../../api/types";

export function importResultSummary(
  overrides: Partial<ImportBatchResultSummary> = {},
): ImportBatchResultSummary {
  return {
    library_root: "C:/Library",
    selected_sources: 1,
    effective_sources: 1,
    discovered_files: 3,
    supported_files: 3,
    unsupported_files: 0,
    reparse_points_skipped: 0,
    scan_failures: 0,
    processed_files: 3,
    succeeded_files: 3,
    failed_files: 0,
    new_assets: 0,
    duplicate_assets: 0,
    source_records_added: 0,
    source_records_refreshed: 0,
    jobs_created: 0,
    failure_count: 0,
    failure_details: [],
    failures_truncated: false,
    active_recipe_id: null,
    ...overrides,
  };
}

export function importSnapshot(
  overrides: Partial<ImportTask> = {},
): ImportTask {
  return {
    batch_id: null,
    status: "idle",
    running: false,
    paused: false,
    pause_requested: false,
    source_folder: null,
    selected_sources: 0,
    effective_sources: 0,
    discovered_files: 0,
    supported_files: 0,
    unsupported_files: 0,
    reparse_points_skipped: 0,
    scan_failures: 0,
    processed_files: 0,
    succeeded_files: 0,
    failed_files: 0,
    new_assets: 0,
    duplicate_assets: 0,
    source_records_added: 0,
    source_records_refreshed: 0,
    jobs_created: 0,
    current_source_name: null,
    started_at: null,
    finished_at: null,
    result: null,
    partial_result: null,
    error: null,
    ...overrides,
  };
}
