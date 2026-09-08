// @ts-expect-error Vitest runs this test in Node; the browser app omits Node types.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs this test in Node; the browser app omits Node types.
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function readAsset(relativePath: string) {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)));
}

function readPngSize(bytes: ReturnType<typeof readAsset>) {
  expect(Array.from(bytes.subarray(0, 8))).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
  expect(bytes[24]).toBe(8);
  expect(bytes[25]).toBe(6);
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe("Windows icon exports", () => {
  it("ships the selected artwork as a high-resolution RGBA title bar icon", () => {
    expect(readPngSize(readAsset("../../assets/memesort-icon.png"))).toEqual([128, 128]);
  });

  it("keeps the configured PNG icons at their expected dimensions", () => {
    const expectedSizes = [
      ["../../../src-tauri/icons/32x32.png", [32, 32]],
      ["../../../src-tauri/icons/128x128.png", [128, 128]],
      ["../../../src-tauri/icons/128x128@2x.png", [256, 256]],
    ] as const;

    for (const [path, size] of expectedSizes) {
      expect(readPngSize(readAsset(path))).toEqual(size);
    }
  });

  it("includes the common Windows ICO sizes with 32-bit alpha", () => {
    const ico = readAsset("../../../src-tauri/icons/icon.ico");

    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);

    const entryCount = ico.readUInt16LE(4);
    const entries = Array.from({ length: entryCount }, (_, index) => {
      const offset = 6 + index * 16;
      return {
        width: ico[offset] || 256,
        height: ico[offset + 1] || 256,
        bitCount: ico.readUInt16LE(offset + 6),
        byteCount: ico.readUInt32LE(offset + 8),
        imageOffset: ico.readUInt32LE(offset + 12),
      };
    });

    expect(entries.map(({ width }) => width).sort((left, right) => left - right)).toEqual([
      16, 24, 32, 48, 64, 128, 256,
    ]);
    for (const entry of entries) {
      expect(entry.height).toBe(entry.width);
      expect(entry.bitCount).toBe(32);
      expect(entry.imageOffset + entry.byteCount).toBeLessThanOrEqual(ico.length);
    }
  });
});
