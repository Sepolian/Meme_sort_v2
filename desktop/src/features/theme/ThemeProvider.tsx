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

/**
 * Theme controller (ticket 18).
 *
 * - Owns the single persisted `system | dark | light` preference.
 * - Resolves `system` through `prefers-color-scheme` and writes only
 *   `data-theme="dark" | "light"` to `<html>`.
 * - While preference is `system`, subscribes to live OS changes and cleans
 *   up the media-query subscription on preference change/unmount.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(() =>
    loadThemePreference(),
  );
  const [systemDark, setSystemDark] = useState<boolean>(() => getSystemDark());

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
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
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
      saveThemePreference(parsed);
      if (parsed === "system") {
        setSystemDark(getSystemDark());
      }
    },
    [],
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
