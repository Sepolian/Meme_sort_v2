export interface AppState {
  library_root: string;
  runtime: {
    backend_name: string;
    device: string;
  };
  setup_state: {
    health_check_ok: boolean;
  };
  library_status: {
    total_assets: number;
    job_counts: Record<string, number>;
  };
  worker_loop: {
    paused: boolean;
    running: boolean;
  };
  pending_jobs: Array<{ job_id: string }>;
}
