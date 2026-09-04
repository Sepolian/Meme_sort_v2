/**
 * Theme preference contract (ticket 18, sole owner of theme persistence).
 *
 * - Preference is exactly `system | dark | light`, defaulting to `system`.
 * - The preference is persisted separately from the resolved appearance under
 *   a versioned localStorage key owned only by this module.
 * - Resolved appearance is always `dark | light`; `system` resolves through
 *   `prefers-color-scheme`. No other feature may read or write the theme key
 *   (ticket 07 explicitly disclaims theme ownership).
 */

export type ThemePreference = "system" | "dark" | "light";
export type ResolvedTheme = "dark" | "light";

export const THEME_PREFERENCES: readonly ThemePreference[] = [
  "system",
  "dark",
  "light",
] as const;

export const DEFAULT_THEME_PREFERENCE: ThemePreference = "system";

/** Versioned storage key. Bumping the suffix invalidates obsolete values. */
export const THEME_PREFERENCE_KEY = "memesort.theme.preference/v1";

export const SYSTEM_THEME_QUERY = "(prefers-color-scheme: dark)";

export function isThemePreference(value: unknown): value is ThemePreference {
  return (
    typeof value === "string" &&
    (THEME_PREFERENCES as readonly string[]).includes(value)
  );
}

/** Parse an unknown stored value; anything invalid falls back to `system`. */
export function parseThemePreference(value: unknown): ThemePreference {
  return isThemePreference(value) ? value : DEFAULT_THEME_PREFERENCE;
}

function defaultStorage(): Storage | null {
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch {
    // Private mode / test environments without localStorage fall through.
  }
  return null;
}

/** Load the persisted preference, falling back to `system` on any failure. */
export function loadThemePreference(
  storage?: Storage | null,
): ThemePreference {
  const store = storage === undefined ? defaultStorage() : storage;
  if (!store) return DEFAULT_THEME_PREFERENCE;
  try {
    return parseThemePreference(store.getItem(THEME_PREFERENCE_KEY));
  } catch {
    return DEFAULT_THEME_PREFERENCE;
  }
}

/** Persist the preference without touching any other key. */
export function saveThemePreference(
  preference: ThemePreference,
  storage?: Storage | null,
): void {
  const store = storage === undefined ? defaultStorage() : storage;
  if (!store) return;
  try {
    store.setItem(THEME_PREFERENCE_KEY, parseThemePreference(preference));
  } catch {
    // Quota/private-mode failures must not break browsing.
  }
}

/**
 * Resolve a preference to a concrete `data-theme` value.
 * `system` follows the OS; explicit `dark`/`light` win unconditionally.
 */
export function resolveTheme(
  preference: ThemePreference,
  systemDark: boolean,
): ResolvedTheme {
  if (preference === "dark") return "dark";
  if (preference === "light") return "light";
  return systemDark ? "dark" : "light";
}

/** Read the current OS appearance. False when matchMedia is unavailable. */
export function getSystemDark(
  matchMediaFn?: typeof window.matchMedia,
): boolean {
  try {
    const fn =
      matchMediaFn ??
      (typeof window !== "undefined" &&
      typeof window.matchMedia === "function"
        ? window.matchMedia.bind(window)
        : null);
    if (!fn) return false;
    return fn(SYSTEM_THEME_QUERY).matches;
  } catch {
    return false;
  }
}

/** Apply the resolved theme to `<html>` (never writes `system`). */
export function applyResolvedTheme(
  resolved: ResolvedTheme,
  doc?: Document,
): void {
  const target =
    doc ?? (typeof document !== "undefined" ? document : null);
  if (!target?.documentElement) return;
  target.documentElement.dataset.theme = resolved;
}

export default THEME_PREFERENCE_KEY;
