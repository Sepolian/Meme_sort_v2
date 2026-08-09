import { describe, expect, it } from "vitest";
import { mediaUrl } from "./media-url";

describe("mediaUrl", () => {
  it("maps only a projected managed media path to the Tauri protocol", () => {
    expect(mediaUrl("/media/thumbnails/asset.jpg")).toBe("http://memesort-media.localhost/media/thumbnails/asset.jpg");
  });

  it("does not turn arbitrary paths into a protocol capability", () => {
    expect(mediaUrl("C:/Users/example.png")).toBeUndefined();
    expect(mediaUrl("https://example.test/image.png")).toBeUndefined();
    expect(mediaUrl(null)).toBeUndefined();
  });
});
