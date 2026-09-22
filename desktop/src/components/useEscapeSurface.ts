import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

type EscapeSurface = {
  onEscape: { current: () => void };
};

// Visible surfaces register in activation order. The newest registration is
// the topmost surface, so one Escape can dismiss only that surface.
const surfaces: EscapeSurface[] = [];
let listenerAttached = false;

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  const surface = surfaces[surfaces.length - 1];
  if (!surface) return;
  event.preventDefault();
  event.stopPropagation();
  surface.onEscape.current();
}

function addSurface(surface: EscapeSurface): () => void {
  surfaces.push(surface);
  if (!listenerAttached) {
    window.addEventListener("keydown", onWindowKeyDown, true);
    listenerAttached = true;
  }
  return () => {
    const index = surfaces.indexOf(surface);
    if (index >= 0) surfaces.splice(index, 1);
    if (!surfaces.length && listenerAttached) {
      window.removeEventListener("keydown", onWindowKeyDown, true);
      listenerAttached = false;
    }
  };
}

export function useEscapeSurface(active: boolean, onEscape: () => void): void {
  const onEscapeRef = useRef(onEscape);
  onEscapeRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    return addSurface({ onEscape: onEscapeRef });
  }, [active]);
}

/** A target is safe to receive restoration focus only while it remains usable. */
export function isRestorableFocusTarget(element: HTMLElement | null): element is HTMLElement {
  return Boolean(
    element?.isConnected &&
      !element.matches(":disabled") &&
      element.getAttribute("aria-disabled") !== "true",
  );
}

/** Schedule focus after React has committed a closing surface. */
export function scheduleFocusRestoration(callback: () => void): () => void {
  const frame = window.requestAnimationFrame(callback);
  return () => window.cancelAnimationFrame(frame);
}

export function supportsModalDialog(): boolean {
  return typeof HTMLDialogElement !== "undefined"
    && typeof HTMLDialogElement.prototype.showModal === "function";
}

/** Open a real modal in WebView; jsdom falls back to the `open` attribute. */
export function useModalDialog(dialogRef: RefObject<HTMLDialogElement | null>): void {
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || typeof dialog.showModal !== "function") return;
    dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [dialogRef]);
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Keep the legacy/jsdom fallback usable; native dialogs trap focus themselves. */
export function useDialogFallback(dialogRef: RefObject<HTMLDialogElement | null>): void {
  useEffect(() => {
    if (supportsModalDialog()) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      const activeIndex = focusable.indexOf(document.activeElement as HTMLElement);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      if (event.shiftKey && activeIndex <= 0) {
        event.preventDefault();
        focusable[focusable.length - 1].focus({ preventScroll: true });
      } else if (!event.shiftKey && activeIndex === focusable.length - 1) {
        event.preventDefault();
        focusable[0].focus({ preventScroll: true });
      }
    };
    dialog.addEventListener("keydown", onKeyDown);
    return () => dialog.removeEventListener("keydown", onKeyDown);
  }, [dialogRef]);
}
