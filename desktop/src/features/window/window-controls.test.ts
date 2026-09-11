import { describe, expect, it, vi } from "vitest";
import { createWindowController, type DesktopWindow } from "./window-controls";

function desktopWindow(overrides: Partial<DesktopWindow> = {}): DesktopWindow {
  return {
    minimize: vi.fn(async () => undefined),
    toggleMaximize: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
    isMaximized: vi.fn(async () => false),
    onResized: vi.fn(async () => () => undefined),
    ...overrides,
  };
}

describe("window controller", () => {
  it("performs native window operations when the desktop host is available", async () => {
    const window = desktopWindow();
    const controller = createWindowController(() => window);

    await expect(controller.minimize()).resolves.toBe(true);
    await expect(controller.toggleMaximize()).resolves.toBe(true);
    await expect(controller.close()).resolves.toBe(true);

    expect(window.minimize).toHaveBeenCalledOnce();
    expect(window.toggleMaximize).toHaveBeenCalledOnce();
    expect(window.close).toHaveBeenCalledOnce();
  });

  it("fails safely without a native desktop window", async () => {
    const controller = createWindowController(() => null);

    await expect(controller.minimize()).resolves.toBe(false);
    await expect(controller.toggleMaximize()).resolves.toBe(false);
    await expect(controller.close()).resolves.toBe(false);
    expect(controller.isNative).toBe(false);
  });

  it("reports a failed native operation without rejecting into the UI", async () => {
    const window = desktopWindow({
      minimize: vi.fn(async () => { throw new Error("permission denied"); }),
    });
    const controller = createWindowController(() => window);

    await expect(controller.minimize()).resolves.toBe(false);
  });

  it("synchronizes maximized state on startup and resize, then cleans up", async () => {
    const onResize = vi.fn();
    const unlisten = vi.fn();
    const isMaximized = vi.fn(async () => false);
    const window = desktopWindow({
      isMaximized,
      onResized: vi.fn(async (listener) => {
        onResize.mockImplementation(listener);
        return unlisten;
      }),
    });
    const controller = createWindowController(() => window);
    const onMaximizedChanged = vi.fn();

    const dispose = controller.subscribeMaximized(onMaximizedChanged);
    await vi.waitFor(() => expect(onMaximizedChanged).toHaveBeenCalledWith(false));

    isMaximized.mockResolvedValueOnce(true);
    onResize();
    await vi.waitFor(() => expect(onMaximizedChanged).toHaveBeenLastCalledWith(true));

    dispose();
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("does not update or leak a listener if unmounted before registration resolves", async () => {
    let resolveListener: (unlisten: () => void) => void = () => undefined;
    const unlisten = vi.fn();
    const window = desktopWindow({
      onResized: vi.fn<(listener: () => void) => Promise<() => void>>(() => new Promise<() => void>((resolve) => {
        resolveListener = resolve;
      })),
    });
    const controller = createWindowController(() => window);
    const onMaximizedChanged = vi.fn();

    const dispose = controller.subscribeMaximized(onMaximizedChanged);
    dispose();
    resolveListener(unlisten);
    await Promise.resolve();

    expect(unlisten).toHaveBeenCalledOnce();
    expect(onMaximizedChanged).not.toHaveBeenCalled();
  });

  it("keeps a newer maximized state when earlier queries resolve late", async () => {
    const resolvers: Array<(value: boolean) => void> = [];
    const onResize = vi.fn();
    const window = desktopWindow({
      isMaximized: vi.fn(() => new Promise<boolean>((resolve) => resolvers.push(resolve))),
      onResized: vi.fn(async (listener) => {
        onResize.mockImplementation(listener);
        return () => undefined;
      }),
    });
    const controller = createWindowController(() => window);
    const onMaximizedChanged = vi.fn();

    controller.subscribeMaximized(onMaximizedChanged);
    await vi.waitFor(() => expect(resolvers).toHaveLength(1));
    onResize();
    await vi.waitFor(() => expect(resolvers).toHaveLength(2));
    resolvers[1](true);
    await vi.waitFor(() => expect(onMaximizedChanged).toHaveBeenCalledWith(true));
    resolvers[0](false);
    await Promise.resolve();

    expect(onMaximizedChanged).toHaveBeenCalledTimes(1);
  });

  it("swallows rejected state and listener setup calls", async () => {
    const window = desktopWindow({
      isMaximized: vi.fn(async () => { throw new Error("unavailable"); }),
      onResized: vi.fn(async () => { throw new Error("unavailable"); }),
    });
    const onMaximizedChanged = vi.fn();

    createWindowController(() => window).subscribeMaximized(onMaximizedChanged);
    await Promise.resolve();
    await Promise.resolve();

    expect(onMaximizedChanged).not.toHaveBeenCalled();
  });
});
