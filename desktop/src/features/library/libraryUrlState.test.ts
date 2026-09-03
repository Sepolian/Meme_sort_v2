import { describe, expect, it, beforeEach } from "vitest";
import {
  DEFAULT_LIBRARY_DENSITY,
  DEFAULT_LIBRARY_MEDIA,
  DEFAULT_LIBRARY_SORT,
  DEFAULT_LIBRARY_STATUS,
  LIBRARY_PREFERENCE_KEYS,
  buildLibrarySearchString,
  loadLibraryPreferences,
  normalizeLibrarySearchString,
  parseLibraryUrlState,
  resolveEffectiveLibraryState,
  saveLibraryPreferences,
  serializeLibraryUrlState,
} from "./libraryUrlState";

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

describe("libraryUrlState parser", () => {
  it("parses every supported sort value", () => {
    for (const sort of ["newest", "oldest", "name", "type", "status"] as const) {
      expect(parseLibraryUrlState(`?sort=${sort}`).sort).toBe(sort);
    }
  });

  it("parses every supported media filter value", () => {
    for (const media of ["all", "still", "gif"] as const) {
      expect(parseLibraryUrlState(`?media=${media}`).media).toBe(media);
    }
  });

  it("parses every supported status filter value", () => {
    for (const status of ["all", "indexed", "pending", "failed"] as const) {
      expect(parseLibraryUrlState(`?status=${status}`).status).toBe(status);
    }
  });

  it("parses q and asset without trimming meaningful content", () => {
    const parsed = parseLibraryUrlState("?q=hello%20world&asset=abc-123");
    expect(parsed.q).toBe("hello world");
    expect(parsed.assetId).toBe("abc-123");
  });

  it("normalizes invalid enum values to absent (defaults apply later)", () => {
    const parsed = parseLibraryUrlState("?sort=bogus&media=raw&status=nope");
    expect(parsed.sort).toBeNull();
    expect(parsed.media).toBeNull();
    expect(parsed.status).toBeNull();
  });

  it("treats empty asset and blank q as absent", () => {
    expect(parseLibraryUrlState("?asset=").assetId).toBeNull();
    expect(parseLibraryUrlState("?q=").q).toBe("");
  });

  it("round-trips encoding for q with special characters", () => {
    const original = "a&b=c+d 中文 meme";
    const serialized = serializeLibraryUrlState({ q: original });
    expect(serialized.get("q")).toBe(original);
    expect(parseLibraryUrlState(`?${serialized.toString()}`).q).toBe(original);
  });

  it("serializes only non-default values to keep URLs minimal", () => {
    const params = serializeLibraryUrlState({
      q: "",
      sort: DEFAULT_LIBRARY_SORT,
      media: DEFAULT_LIBRARY_MEDIA,
      status: DEFAULT_LIBRARY_STATUS,
      assetId: null,
    });
    expect(params.toString()).toBe("");

    const explicit = serializeLibraryUrlState({
      q: "cat",
      sort: "oldest",
      media: "gif",
      status: "failed",
      assetId: "id-1",
    });
    expect(explicit.get("q")).toBe("cat");
    expect(explicit.get("sort")).toBe("oldest");
    expect(explicit.get("media")).toBe("gif");
    expect(explicit.get("status")).toBe("failed");
    expect(explicit.get("asset")).toBe("id-1");
  });

  it("builds a search string with a leading question mark only when needed", () => {
    expect(buildLibrarySearchString({ q: "" })).toBe("");
    expect(buildLibrarySearchString({ q: "hi" })).toBe("?q=hi");
  });

  it("detects and removes invalid enum values without touching valid params", () => {
    const raw = "?sort=bogus&q=keep&media=gif&status=nope&asset=keep-me";
    const normalized = normalizeLibrarySearchString(raw);
    const parsed = parseLibraryUrlState(normalized);
    expect(parsed.q).toBe("keep");
    expect(parsed.media).toBe("gif");
    expect(parsed.assetId).toBe("keep-me");
    expect(parsed.sort).toBeNull();
    expect(parsed.status).toBeNull();
    // Invalid values are gone; valid ones survive.
    expect(normalized).not.toContain("bogus");
    expect(normalized).not.toContain("nope");
    expect(normalized).toContain("keep");
  });
});

describe("library preferences", () => {
  let storage: Storage;

  beforeEach(() => {
    storage = makeStorage();
  });

  it("falls back to defaults when storage is empty", () => {
    expect(loadLibraryPreferences(storage)).toEqual({
      sort: "newest",
      media: "all",
      status: "all",
      density: "comfortable",
    });
  });

  it("restores persisted sort/media/status/density", () => {
    saveLibraryPreferences({ sort: "oldest", media: "gif", status: "failed", density: "compact" }, storage);
    expect(loadLibraryPreferences(storage)).toEqual({
      sort: "oldest",
      media: "gif",
      status: "failed",
      density: "compact",
    });
  });

  it("falls back to defaults for invalid and obsolete stored values", () => {
    storage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "obsolete-value");
    storage.setItem(LIBRARY_PREFERENCE_KEYS.media, "RAW");
    storage.setItem(LIBRARY_PREFERENCE_KEYS.status, "");
    storage.setItem(LIBRARY_PREFERENCE_KEYS.density, "cozy");
    expect(loadLibraryPreferences(storage)).toEqual({
      sort: DEFAULT_LIBRARY_SORT,
      media: DEFAULT_LIBRARY_MEDIA,
      status: DEFAULT_LIBRARY_STATUS,
      density: DEFAULT_LIBRARY_DENSITY,
    });
  });

  it("persists partial updates without clobbering other keys", () => {
    saveLibraryPreferences({ sort: "name" }, storage);
    saveLibraryPreferences({ density: "compact" }, storage);
    const loaded = loadLibraryPreferences(storage);
    expect(loaded.sort).toBe("name");
    expect(loaded.density).toBe("compact");
    expect(loaded.media).toBe("all");
  });

  it("uses versioned preference keys", () => {
    for (const key of Object.values(LIBRARY_PREFERENCE_KEYS)) {
      expect(key).toMatch(/v1/);
    }
  });
});

describe("precedence", () => {
  it("prefers URL over persisted preference over default", () => {
    const prefs = { sort: "oldest" as const, media: "gif" as const, status: "failed" as const, density: "compact" as const };

    // URL wins.
    expect(
      resolveEffectiveLibraryState(parseLibraryUrlState("?sort=name&media=still&status=indexed"), prefs).sort,
    ).toBe("name");

    // No URL -> persisted wins.
    const noUrl = resolveEffectiveLibraryState(parseLibraryUrlState(""), prefs);
    expect(noUrl.sort).toBe("oldest");
    expect(noUrl.media).toBe("gif");
    expect(noUrl.status).toBe("failed");
    expect(noUrl.density).toBe("compact");

    // Neither URL nor valid persisted -> default.
    const emptyPrefs = { sort: "newest" as const, media: "all" as const, status: "all" as const, density: "comfortable" as const };
    const fallback = resolveEffectiveLibraryState(parseLibraryUrlState(""), emptyPrefs);
    expect(fallback.sort).toBe("newest");
  });

  it("keeps q and asset as URL-only transient state", () => {
    const prefs = loadLibraryPreferences(makeStorage());
    const resolved = resolveEffectiveLibraryState(parseLibraryUrlState("?q=cat&asset=id-9"), prefs);
    expect(resolved.q).toBe("cat");
    expect(resolved.assetId).toBe("id-9");
  });
});
