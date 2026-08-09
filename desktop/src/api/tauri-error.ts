export interface TauriCommandError {
  status: number | null;
  error: string;
  detail: string;
  retryable: boolean;
}

export function tauriErrorDetail(error: unknown, fallback: string): string {
  if (
    typeof error === "object"
    && error !== null
    && typeof (error as Partial<TauriCommandError>).detail === "string"
  ) {
    return (error as TauriCommandError).detail;
  }
  return error instanceof Error ? error.message : fallback;
}
