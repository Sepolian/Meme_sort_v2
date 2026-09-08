import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useEffect, useState } from "react";

export interface DesktopWindow {
  minimize(): Promise<void>;
  toggleMaximize(): Promise<void>;
  close(): Promise<void>;
  startDragging(): Promise<void>;
  isMaximized(): Promise<boolean>;
  onResized(listener: () => void): Promise<() => void>;
}

export interface WindowControls {
  isNative: boolean;
  isMaximized: boolean;
  minimize(): Promise<boolean>;
  toggleMaximize(): Promise<boolean>;
  close(): Promise<boolean>;
  startDragging(): Promise<boolean>;
}

export interface WindowController extends Omit<WindowControls, "isMaximized"> {
  subscribeMaximized(listener: (maximized: boolean) => void): () => void;
}

type WindowProvider = () => DesktopWindow | null;

/**
 * Isolates optional desktop APIs from the React shell. Calls are deliberately
 * best-effort: Vite preview has no Tauri transport and must remain usable.
 */
export function createWindowController(getWindow: WindowProvider): WindowController {
  const getNativeWindow = (): DesktopWindow | null => {
    try {
      return getWindow();
    } catch {
      return null;
    }
  };

  const invoke = async (operation: (window: DesktopWindow) => Promise<void>): Promise<boolean> => {
    const window = getNativeWindow();
    if (!window) return false;
    try {
      await operation(window);
      return true;
    } catch {
      return false;
    }
  };

  return {
    get isNative() {
      return getNativeWindow() !== null;
    },
    minimize: () => invoke((window) => window.minimize()),
    toggleMaximize: () => invoke((window) => window.toggleMaximize()),
    close: () => invoke((window) => window.close()),
    startDragging: () => invoke((window) => window.startDragging()),
    subscribeMaximized(listener) {
      const window = getNativeWindow();
      if (!window) return () => undefined;

      let disposed = false;
      let unlisten: (() => void) | null = null;
      let latestSync = 0;
      const sync = async () => {
        const syncId = ++latestSync;
        try {
          const maximized = await window.isMaximized();
          if (!disposed && syncId === latestSync) listener(maximized);
        } catch {
          // Window events and state queries are unavailable outside Tauri.
        }
      };

      void sync();
      void window.onResized(() => void sync()).then(
        (dispose) => {
          if (disposed) dispose();
          else unlisten = dispose;
        },
        () => undefined,
      );

      return () => {
        disposed = true;
        unlisten?.();
      };
    },
  };
}

const desktopWindowController = createWindowController(() => (isTauri() ? getCurrentWindow() : null));

export function useWindowControls(controller = desktopWindowController): WindowControls {
  const [isMaximized, setIsMaximized] = useState(false);

  useEffect(() => controller.subscribeMaximized(setIsMaximized), [controller]);

  return {
    isNative: controller.isNative,
    isMaximized,
    minimize: controller.minimize,
    toggleMaximize: controller.toggleMaximize,
    close: controller.close,
    startDragging: controller.startDragging,
  };
}
