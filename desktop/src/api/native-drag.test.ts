import { describe, expect, it, vi } from "vitest";
import { subscribeNativeDrag, NATIVE_DRAG_EVENT, type NativeDragListener } from "./native-drag";

vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

import { listen } from "@tauri-apps/api/event";

const listenMock = vi.mocked(listen);

type EventPayload = Record<string, unknown>;

function emittedHandler(): { handler: (event: { payload: unknown }) => void } {
  const calls = listenMock.mock.calls;
  const last = calls[calls.length - 1];
  return { handler: last[1] as (event: { payload: unknown }) => void };
}

describe("subscribeNativeDrag", () => {
  it("listens only to the fixed native drag event", () => {
    listenMock.mockReset();
    listenMock.mockResolvedValue(() => undefined);

    subscribeNativeDrag(() => undefined);

    expect(listenMock).toHaveBeenCalledTimes(1);
    expect(listenMock).toHaveBeenCalledWith(NATIVE_DRAG_EVENT, expect.any(Function));
  });

  it("forwards path-free summaries with camelCase fields", () => {
    listenMock.mockReset();
    listenMock.mockResolvedValue(() => undefined);
    const listener = vi.fn<NativeDragListener>();
    subscribeNativeDrag(listener);

    emittedHandler().handler({
      payload: {
        phase: "enter",
        file_count: 2,
        folder_count: 3,
        x: 120.5,
        y: 64,
        accepted: true,
      } satisfies EventPayload,
    });

    expect(listener).toHaveBeenCalledWith({
      phase: "enter",
      fileCount: 2,
      folderCount: 3,
      x: 120.5,
      y: 64,
      accepted: true,
      dropId: null,
    });
  });

  it("keeps the one-time drop ID but nothing else from drops", () => {
    listenMock.mockReset();
    listenMock.mockResolvedValue(() => undefined);
    const listener = vi.fn<NativeDragListener>();
    subscribeNativeDrag(listener);

    emittedHandler().handler({
      payload: {
        phase: "drop",
        file_count: 1,
        folder_count: 0,
        x: 10,
        y: 20,
        accepted: true,
        drop_id: "123e4567-e89b-12d3-a456-426614174009",
      } satisfies EventPayload,
    });

    expect(listener).toHaveBeenCalledWith({
      phase: "drop",
      fileCount: 1,
      folderCount: 0,
      x: 10,
      y: 20,
      accepted: true,
      dropId: "123e4567-e89b-12d3-a456-426614174009",
    });
  });

  it("ignores payloads that are not native drag phases", () => {
    listenMock.mockReset();
    listenMock.mockResolvedValue(() => undefined);
    const listener = vi.fn<NativeDragListener>();
    subscribeNativeDrag(listener);

    emittedHandler().handler({ payload: { phase: "explode", paths: ["C:/Secret"] } });
    emittedHandler().handler({ payload: null });

    expect(listener).not.toHaveBeenCalled();
  });

  it("unlistens when disposed before or after the subscription resolves", async () => {
    listenMock.mockReset();
    const unlistenEarly = vi.fn();
    let resolveLate: (value: () => void) => void = () => undefined;
    listenMock
      .mockResolvedValueOnce(unlistenEarly)
      .mockImplementationOnce(
        () =>
          new Promise<() => void>((resolve) => {
            resolveLate = resolve;
          }),
      );

    const disposeEarly = subscribeNativeDrag(() => undefined);
    disposeEarly();
    await Promise.resolve();
    expect(unlistenEarly).toHaveBeenCalled();

    const unlistenLate = vi.fn();
    const disposeLate = subscribeNativeDrag(() => undefined);
    disposeLate();
    resolveLate(unlistenLate);
    await Promise.resolve();
    expect(unlistenLate).toHaveBeenCalled();
  });

  it("stays quiet when the desktop event channel is unavailable", async () => {
    listenMock.mockReset();
    listenMock.mockRejectedValueOnce(new Error("not running inside Tauri"));

    expect(() => subscribeNativeDrag(() => undefined)).not.toThrow();
    await Promise.resolve();
    await Promise.resolve();
  });
});
