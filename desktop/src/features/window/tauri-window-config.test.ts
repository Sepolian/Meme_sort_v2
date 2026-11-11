// @ts-expect-error Vitest runs this test in Node; the browser app omits Node types.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs this test in Node; the browser app omits Node types.
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function readProjectJson<T>(relativePath: string): T {
  return JSON.parse(readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), "utf8")) as T;
}

describe("native window configuration", () => {
  it("uses a resizable, opaque, undecorated main window with the planned bounds", () => {
    const config = readProjectJson<{
      app: { windows: Array<{ width: number; height: number; minWidth: number; minHeight: number; decorations: boolean; resizable: boolean; transparent: boolean }> };
    }>("../../../src-tauri/tauri.conf.json");

    expect(config.app.windows).toContainEqual(expect.objectContaining({
      width: 1200,
      height: 800,
      minWidth: 900,
      minHeight: 600,
      decorations: false,
      resizable: true,
      transparent: false,
    }));
  });

  it("keeps the Windows media CSP and limits the Mac override to its exact native media origin", () => {
    type Config = { app: { security?: { csp: string } } };
    const windows = readProjectJson<Config>("../../../src-tauri/tauri.conf.json");
    const mac = readProjectJson<Config>("../../../src-tauri/tauri.macos.conf.json");

    expect(windows.app.security?.csp).toBe("default-src 'self'; connect-src ipc: http://ipc.localhost http://localhost:1420 ws://localhost:1420; img-src 'self' http://memesort-media.localhost; media-src 'self' http://memesort-media.localhost; style-src 'self' 'unsafe-inline'");
    expect(mac.app.security?.csp).toBe("default-src 'self'; connect-src ipc: http://ipc.localhost http://localhost:1420 ws://localhost:1420; img-src 'self' memesort-media://localhost; media-src 'self' memesort-media://localhost; style-src 'self' 'unsafe-inline'");
  });

  it("uses normal native decorations for the Mac app", () => {
    const config = readProjectJson<{ app: { windows: Array<{ label: string; decorations: boolean }> }; bundle: { active: boolean; targets: string[]; resources: Record<string, string> } }>("../../../src-tauri/tauri.macos.conf.json");
    expect(config.app.windows).toContainEqual(expect.objectContaining({ label: "main", decorations: true }));
    expect(config.bundle).toMatchObject({ active: true, targets: ["app"] });
    expect(Object.values(config.bundle.resources)).toContain("sidecar/");
  });

  it("grants the window operations used by the title bar", () => {
    const capability = readProjectJson<{ permissions: string[] }>("../../../src-tauri/capabilities/default.json");

    expect(capability.permissions).toEqual(expect.arrayContaining([
      "core:window:allow-close",
      "core:window:allow-is-maximized",
      "core:window:allow-minimize",
      "core:window:allow-start-dragging",
      "core:window:allow-toggle-maximize",
      "core:window:allow-internal-toggle-maximize",
    ]));
  });
});
