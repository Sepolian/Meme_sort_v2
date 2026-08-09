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
  pending_jobs: Array<{ job_id: string }>;
}
