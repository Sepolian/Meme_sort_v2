import { describe, expect, it, beforeEach } from "vitest";
import {
  DEFAULT_THEME_PREFERENCE,
  THEME_PREFERENCE_KEY,
  applyResolvedTheme,
  getSystemDark,
  isThemePreference,
  loadThemePreference,
  parseThemePreference,
  resolveTheme,
  saveThemePreference,
} from "./theme";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
  } as Storage;
}

describe("theme preference contract", () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  it("defaults first launch to system", () => {
    expect(DEFAULT_THEME_PREFERENCE).toBe("system");
    expect(parseThemePreference(null)).toBe("system");
    expect(parseThemePreference(undefined)).toBe("system");
    expect(parseThemePreference("")).toBe("system");
    expect(loadThemePreference(memoryStorage())).toBe("system");
    // Empty browser storage also resolves to system preference.
    expect(loadThemePreference(window.localStorage)).toBe("system");
  });

  it("accepts exactly system, dark, and light", () => {
    expect(isThemePreference("system")).toBe(true);
    expect(isThemePreference("dark")).toBe(true);
    expect(isThemePreference("light")).toBe(true);
    expect(isThemePreference("SYSTEM")).toBe(false);
    expect(isThemePreference("")).toBe(false);
    expect(isThemePreference(null)).toBe(false);
    expect(isThemePreference(42)).toBe(false);
    expect(parseThemePreference("dark")).toBe("dark");
    expect(parseThemePreference("light")).toBe("light");
    expect(parseThemePreference("system")).toBe("system");
  });

  it("falls back to system for invalid stored values", () => {
    expect(
      loadThemePreference(memoryStorage({ [THEME_PREFERENCE_KEY]: "sepia" })),
    ).toBe("system");
    expect(
      loadThemePreference(memoryStorage({ [THEME_PREFERENCE_KEY]: "" })),
    ).toBe("system");
    expect(
      loadThemePreference(memoryStorage({ [THEME_PREFERENCE_KEY]: "DARK" })),
    ).toBe("system");
  });

  it("persists and restores all three preference values separately", () => {
    const storage = memoryStorage();
    for (const preference of ["system", "dark", "light"] as const) {
      saveThemePreference(preference, storage);
      expect(storage.getItem(THEME_PREFERENCE_KEY)).toBe(preference);
      expect(loadThemePreference(storage)).toBe(preference);
    }
  });

  it("persists the preference separately from the resolved appearance", () => {
    const storage = memoryStorage();
    // Preference stays `system` even when the OS resolves dark.
    saveThemePreference("system", storage);
    expect(storage.getItem(THEME_PREFERENCE_KEY)).toBe("system");
    expect(resolveTheme(loadThemePreference(storage), true)).toBe("dark");
    expect(resolveTheme(loadThemePreference(storage), false)).toBe("light");
    // Explicit preferences ignore the OS value.
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
  });

  it("resolves system through the OS appearance", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });

  it("applies only dark or light to <html>", () => {
    applyResolvedTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    applyResolvedTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("reads the OS appearance without throwing when matchMedia is missing", () => {
    expect(getSystemDark(() => ({ matches: true }) as MediaQueryList)).toBe(
      true,
    );
    expect(getSystemDark(() => ({ matches: false }) as MediaQueryList)).toBe(
      false,
    );
    expect(
      getSystemDark(() => {
        throw new Error("unavailable");
      }),
    ).toBe(false);
  });
});
