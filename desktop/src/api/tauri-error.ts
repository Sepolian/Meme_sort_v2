export interface TauriCommandError {
  status: number | null;
  error: string;
  detail: string;
  retryable: boolean;
}

/** Native dialogs normally report cancellation as a null selection. */
export function isNativePickerCancellation(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const value = error as {
    cancelled?: unknown;
    canceled?: unknown;
    code?: unknown;
    error?: unknown;
  };
  if (value.cancelled === true || value.canceled === true) return true;
  return [value.code, value.error].some(
    (candidate) => typeof candidate === "string" && /^(?:dialog|picker|user)?\s*cancel(?:led|ed)$/i.test(candidate.trim()),
  );
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

export function tauriErrorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const value = error as { code?: unknown; error?: unknown };
  if (typeof value.error === "string") return value.error;
  return typeof value.code === "string" ? value.code : null;
}
