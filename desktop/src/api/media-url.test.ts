import { afterEach, describe, expect, it, vi } from "vitest";
import { convertFileSrc, isTauri } from "@tauri-apps/api/core";
import { mediaUrl } from "./media-url";

vi.mock("@tauri-apps/api/core", () => ({
  convertFileSrc: vi.fn(),
  isTauri: vi.fn(() => false),
}));

afterEach(() => vi.resetAllMocks());

describe("mediaUrl", () => {
  it.each([
    ["macOS", "memesort-media://localhost/"],
    ["Windows", "http://memesort-media.localhost/"],
  ])("uses the native %s media origin without encoding the projected route", (_platform, origin) => {
    vi.mocked(isTauri).mockReturnValue(true);
    vi.mocked(convertFileSrc).mockReturnValue(origin);

    expect(mediaUrl("/media/originals/asset.gif")).toBe(`${origin}media/originals/asset.gif`);
    expect(convertFileSrc).toHaveBeenCalledWith("", "memesort-media");
  });

  it("maps only a projected managed media path to the Tauri protocol", () => {
    expect(mediaUrl("/media/thumbnails/asset.jpg")).toBe("http://memesort-media.localhost/media/thumbnails/asset.jpg");
  });

  it("does not turn arbitrary paths into a protocol capability", () => {
    expect(mediaUrl("C:/Users/example.png")).toBeUndefined();
    expect(mediaUrl("https://example.test/image.png")).toBeUndefined();
    expect(mediaUrl(null)).toBeUndefined();
  });
});
