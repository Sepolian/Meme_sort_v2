import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { useLibraryUrlState } from "./useLibraryUrlState";
import { LIBRARY_PREFERENCE_KEYS } from "./libraryUrlState";

function Probe({ storage, initialMode }: { storage?: Storage | null; initialMode?: "semantic" }) {
  const state = useLibraryUrlState(storage);
  return (
    <div>
      <span data-testid="q">{state.q}</span>
      <span data-testid="sort">{state.sort}</span>
      <span data-testid="media">{state.media}</span>
      <span data-testid="status">{state.status}</span>
      <span data-testid="density">{state.density}</span>
      <span data-testid="asset">{state.assetId ?? "none"}</span>
      <span data-testid="mode">{state.resultMode.kind}</span>
      <span data-testid="mode-query">
        {state.resultMode.kind === "local" || state.resultMode.kind === "semantic" ? state.resultMode.query : ""}
      </span>
      <button type="button" onClick={() => state.setSort("oldest")}>set-sort</button>
      <button type="button" onClick={() => state.setQuery("hello")}>set-query</button>
      <button type="button" onClick={() => state.setQuery("other")}>set-other-query</button>
      <button type="button" onClick={() => state.setAssetId("asset-1")}>set-asset</button>
      <button type="button" onClick={() => state.clearAssetId()}>clear-asset</button>
      <button type="button" onClick={() => state.setDensity("compact")}>set-density</button>
      <button
        type="button"
        onClick={() => state.setResultMode({ kind: "semantic", query: "hello", requestId: "req-1" })}
      >
        set-semantic
      </button>
      {initialMode === "semantic" ? <span data-testid="has-semantic-setup">setup</span> : null}
    </div>
  );
}

function LocationProbe() {
  const [params] = useSearchParams();
  return <span data-testid="location">{`?${params.toString()}`}</span>;
}

function renderProbe(route: string, storage?: Storage | null) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Probe storage={storage} />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function makeStorage(initial: Record<string, string> = {}): Storage {
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

describe("useLibraryUrlState", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = makeStorage();
  });

  it("uses defaults when URL and storage are empty, with browse mode", () => {
    renderProbe("/", storage);
    expect(screen.getByTestId("sort")).toHaveTextContent("newest");
    expect(screen.getByTestId("media")).toHaveTextContent("all");
    expect(screen.getByTestId("status")).toHaveTextContent("all");
    expect(screen.getByTestId("density")).toHaveTextContent("comfortable");
    expect(screen.getByTestId("mode")).toHaveTextContent("browse");
  });

  it("prefers URL over persisted preferences", () => {
    storage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "oldest");
    renderProbe("/?sort=name", storage);
    expect(screen.getByTestId("sort")).toHaveTextContent("name");
  });

  it("restores persisted preferences when URL does not override", () => {
    storage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "oldest");
    storage.setItem(LIBRARY_PREFERENCE_KEYS.media, "gif");
    storage.setItem(LIBRARY_PREFERENCE_KEYS.density, "compact");
    renderProbe("/", storage);
    expect(screen.getByTestId("sort")).toHaveTextContent("oldest");
    expect(screen.getByTestId("media")).toHaveTextContent("gif");
    expect(screen.getByTestId("density")).toHaveTextContent("compact");
  });

  it("derives local mode from URL q without entering semantic mode", () => {
    renderProbe("/?q=cat", storage);
    expect(screen.getByTestId("q")).toHaveTextContent("cat");
    expect(screen.getByTestId("mode")).toHaveTextContent("local");
  });

  it("normalizes invalid URL values to defaults without a reload loop", () => {
    renderProbe("/?sort=bogus&media=gif", storage);
    expect(screen.getByTestId("sort")).toHaveTextContent("newest");
    expect(screen.getByTestId("media")).toHaveTextContent("gif");
    expect(screen.getByTestId("location").textContent).not.toContain("bogus");
  });

  it("updating sort writes both the URL and persisted preference", () => {
    renderProbe("/", storage);
    act(() => {
      screen.getByRole("button", { name: "set-sort" }).click();
    });
    expect(screen.getByTestId("sort")).toHaveTextContent("oldest");
    expect(screen.getByTestId("location").textContent).toContain("sort=oldest");
    expect(storage.getItem(LIBRARY_PREFERENCE_KEYS.sort)).toBe("oldest");
  });

  it("clearing the inspector removes only asset and preserves other params", () => {
    renderProbe("/?q=cat&sort=oldest&asset=asset-9", storage);
    expect(screen.getByTestId("asset")).toHaveTextContent("asset-9");
    act(() => {
      screen.getByRole("button", { name: "clear-asset" }).click();
    });
    expect(screen.getByTestId("asset")).toHaveTextContent("none");
    const location = screen.getByTestId("location").textContent ?? "";
    expect(location).toContain("q=cat");
    expect(location).toContain("sort=oldest");
    expect(location).not.toContain("asset=");
  });

  it("persists density without touching the URL", () => {
    renderProbe("/", storage);
    act(() => {
      screen.getByRole("button", { name: "set-density" }).click();
    });
    expect(screen.getByTestId("density")).toHaveTextContent("compact");
    expect(storage.getItem(LIBRARY_PREFERENCE_KEYS.density)).toBe("compact");
    expect(screen.getByTestId("location").textContent).not.toContain("density");
  });

  it("restores persisted preferences on remount while transient modes stay local", async () => {
    const { unmount } = renderProbe("/", storage);
    act(() => {
      screen.getByRole("button", { name: "set-sort" }).click();
      screen.getByRole("button", { name: "set-density" }).click();
      screen.getByRole("button", { name: "set-asset" }).click();
    });
    expect(storage.getItem(LIBRARY_PREFERENCE_KEYS.sort)).toBe("oldest");
    unmount();

    // Remount with a clean URL but the same storage: persisted prefs return,
    // transient inspector/mode do not leak across sessions.
    renderProbe("/", storage);
    expect(screen.getByTestId("sort")).toHaveTextContent("oldest");
    expect(screen.getByTestId("density")).toHaveTextContent("compact");
    expect(screen.getByTestId("asset")).toHaveTextContent("none");
    expect(screen.getByTestId("mode")).toHaveTextContent("browse");
  });

  it("never restores image/similar modes from URL or storage", () => {
    // Unknown params must not create a persisted image/similar mode.
    renderProbe("/?q=cat&image=foo&similar=bar", storage);
    const mode = screen.getByTestId("mode").textContent;
    expect(mode).toBe("local");
    expect(storage.getItem("memesort.library.mode/v1")).toBeNull();
  });

  it("drops stale semantic results when the URL query changes", () => {
    renderProbe("/?q=hello", storage);
    expect(screen.getByTestId("mode")).toHaveTextContent("local");
    act(() => {
      screen.getByRole("button", { name: "set-semantic" }).click();
    });
    expect(screen.getByTestId("mode")).toHaveTextContent("semantic");
    act(() => {
      screen.getByRole("button", { name: "set-other-query" }).click();
    });
    expect(screen.getByTestId("q")).toHaveTextContent("other");
    expect(screen.getByTestId("mode")).toHaveTextContent("local");
    expect(screen.getByTestId("mode-query")).toHaveTextContent("other");
  });
});
