import { fireEvent, render, screen, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTheme } from "./ThemeContext";
import { ThemeProvider } from "./ThemeProvider";
import {
  THEME_PREFERENCE_KEY,
  type ThemePreference,
} from "./theme";
import { saveLibraryPreferences } from "../library/libraryUrlState";

interface MockQuery {
  matches: boolean;
  listeners: Set<(event: { matches: boolean }) => void>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  dispatch: (matches: boolean) => void;
}

function installMatchMediaMock(initialDark: boolean): MockQuery {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const mock: MockQuery = {
    matches: initialDark,
    listeners,
    addEventListener: vi.fn(
      (_type: string, listener: (event: { matches: boolean }) => void) => {
        listeners.add(listener);
      },
    ),
    removeEventListener: vi.fn(
      (_type: string, listener: (event: { matches: boolean }) => void) => {
        listeners.delete(listener);
      },
    ),
    dispatch: (matches: boolean) => {
      mock.matches = matches;
      for (const listener of [...listeners]) listener({ matches });
    },
  };
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn(() => ({
      matches: mock.matches,
      addEventListener: mock.addEventListener,
      removeEventListener: mock.removeEventListener,
    })),
  });
  // Keep the mock query object in sync: window.matchMedia must return a live
  // object whose `matches` reflects dispatches. Re-implement value to close
  // over the mock.
  (window.matchMedia as ReturnType<typeof vi.fn>).mockImplementation(() => ({
    get matches() {
      return mock.matches;
    },
    addEventListener: mock.addEventListener,
    removeEventListener: mock.removeEventListener,
  }));
  return mock;
}

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <div>
      <span data-testid="preference">{preference}</span>
      <span data-testid="resolved">{resolved}</span>
      {(["system", "dark", "light"] as const).map((value: ThemePreference) => (
        <button
          key={value}
          type="button"
          onClick={() => setPreference(value)}
        >
          use {value}
        </button>
      ))}
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
    // Remove any matchMedia mock from a previous test.
    if ("matchMedia" in window) {
      try {
        // jsdom has no matchMedia by default; tests install their own.
        delete (window as unknown as Record<string, unknown>).matchMedia;
      } catch {
        Object.defineProperty(window, "matchMedia", {
          configurable: true,
          writable: true,
          value: undefined,
        });
      }
    }
  });

  it("uses system on first launch and resolves it to <html>", () => {
    installMatchMediaMock(false);
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("preference")).toHaveTextContent("system");
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBeNull();
  });

  it("persists and restores all three preferences", () => {
    installMatchMediaMock(false);
    const { unmount } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "use dark" }));
    expect(screen.getByTestId("preference")).toHaveTextContent("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "use light" }));
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: "use system" }));
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBe("system");
    unmount();

    // Restore persists across remounts.
    window.localStorage.setItem(THEME_PREFERENCE_KEY, "dark");
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("preference")).toHaveTextContent("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("responds to live OS appearance changes while preference is system", () => {
    const mock = installMatchMediaMock(false);
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    act(() => {
      mock.dispatch(true);
    });
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    // Preference stays `system`; only the resolved appearance follows the OS.
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBeNull();
    expect(screen.getByTestId("preference")).toHaveTextContent("system");
  });

  it("ignores OS changes for explicit dark/light preferences", () => {
    const mock = installMatchMediaMock(false);
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "use dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    act(() => {
      mock.dispatch(false);
    });
    // Explicit dark stays dark regardless of OS updates.
    expect(document.documentElement.dataset.theme).toBe("dark");
    // No live subscription while explicit.
    expect(mock.addEventListener).toHaveBeenCalledTimes(1);
  });

  it("cleans up the media-query subscription on unmount and on explicit switch", () => {
    const mock = installMatchMediaMock(false);
    const { unmount } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(mock.addEventListener).toHaveBeenCalledTimes(1);
    expect(mock.removeEventListener).not.toHaveBeenCalled();
    // Switching to an explicit preference unsubscribes.
    fireEvent.click(screen.getByRole("button", { name: "use dark" }));
    expect(mock.removeEventListener).toHaveBeenCalledTimes(1);
    // Switching back to system resubscribes.
    fireEvent.click(screen.getByRole("button", { name: "use system" }));
    expect(mock.addEventListener).toHaveBeenCalledTimes(2);

    unmount();
    // Unmount removes the active system subscription.
    expect(mock.removeEventListener).toHaveBeenCalledTimes(2);
  });

  it("keeps library preferences from writing the theme key", () => {
    window.localStorage.setItem(THEME_PREFERENCE_KEY, "dark");
    saveLibraryPreferences(
      { sort: "oldest", density: "compact" },
      window.localStorage,
    );
    expect(window.localStorage.getItem(THEME_PREFERENCE_KEY)).toBe("dark");
  });
});
