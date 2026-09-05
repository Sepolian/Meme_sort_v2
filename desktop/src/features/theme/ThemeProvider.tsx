import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  SYSTEM_THEME_QUERY,
  getSystemDark,
  loadThemePreference,
  parseThemePreference,
  resolveTheme,
  saveThemePreference,
  type ThemePreference,
} from "./theme";
import { ThemeContext } from "./ThemeContext";

function resolveStorage(explicit: Storage | null | undefined): Storage | null {
  if (explicit !== undefined) return explicit;
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch {
    return null;
  }
  return null;
}

interface ThemeProviderProps {
  children: ReactNode;
  /** Injected storage for tests; defaults to localStorage. */
  storage?: Storage | null;
  /** Test-only initial preference override (still persisted on change). */
  initialPreference?: ThemePreference;
}

/**
 * Theme controller (ticket 18).
 *
 * - Owns the single persisted `system | dark | light` preference.
 * - Resolves `system` through `prefers-color-scheme` and writes only
 *   `data-theme="dark" | "light"` to `<html>`.
 * - While preference is `system`, subscribes to live OS changes and cleans
 *   up the media-query subscription on preference change/unmount.
 */
export function ThemeProvider({
  children,
  storage: explicitStorage,
  initialPreference,
}: ThemeProviderProps) {
  const storage = useMemo(
    () => resolveStorage(explicitStorage),
    [explicitStorage],
  );
  const [preference, setPreferenceState] = useState<ThemePreference>(() =>
    initialPreference !== undefined
      ? parseThemePreference(initialPreference)
      : loadThemePreference(storage),
  );
  const [systemDark, setSystemDark] = useState<boolean>(() => getSystemDark());

  // Re-read persisted preference when a different storage instance is
  // injected (tests) or when the provider remounts.
  useEffect(() => {
    if (initialPreference === undefined) {
      setPreferenceState(loadThemePreference(storage));
    }
  }, [storage, initialPreference]);

  // Live OS appearance subscription: active only while preference is
  // `system`. Cleanup removes the listener on preference change/unmount.
  useEffect(() => {
    if (preference !== "system") return;
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia(SYSTEM_THEME_QUERY);
    const sync = () => setSystemDark(query.matches);
    sync();
    const onChange = (event: MediaQueryListEvent) => {
      setSystemDark(event.matches);
    };
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    // Legacy Safari (<14) fallback.
    const legacy = query as unknown as {
      addListener?: (listener: (event: MediaQueryListEvent) => void) => void;
      removeListener?: (listener: (event: MediaQueryListEvent) => void) => void;
    };
    if (typeof legacy.addListener === "function") {
      legacy.addListener(onChange);
      return () => legacy.removeListener?.(onChange);
    }
    return;
  }, [preference]);

  const resolved = resolveTheme(preference, systemDark);

  useEffect(() => {
    try {
      document.documentElement.dataset.theme = resolved;
    } catch {
      // Non-DOM environments (SSR) have no document to update.
    }
  }, [resolved]);

  const setPreference = useCallback(
    (next: ThemePreference) => {
      const parsed = parseThemePreference(next);
      setPreferenceState(parsed);
      saveThemePreference(parsed, storage);
      if (parsed === "system") {
        setSystemDark(getSystemDark());
      }
    },
    [storage],
  );

  const value = useMemo(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export default ThemeProvider;
