import { useEffect, useRef, type RefObject } from "react";

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

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** A target is safe to receive restoration focus only while it remains usable. */
export function isRestorableFocusTarget(element: HTMLElement | null): element is HTMLElement {
  return Boolean(
    element?.isConnected &&
      !element.matches(":disabled") &&
      element.getAttribute("aria-disabled") !== "true",
  );
}

let focusRestorationGeneration = 0;
const pendingFocusFrames = new Set<number>();

/** Cancel focus work belonging to a surface that has been replaced. */
export function invalidateFocusRestorations(): void {
  focusRestorationGeneration += 1;
  for (const frame of pendingFocusFrames) window.cancelAnimationFrame(frame);
  pendingFocusFrames.clear();
}

/** Schedule one generation-checked focus frame and return its cancellation hook. */
export function scheduleFocusRestoration(callback: () => void): () => void {
  const generation = focusRestorationGeneration;
  let frame: number | null = null;
  const cancel = () => {
    if (frame === null) return;
    window.cancelAnimationFrame(frame);
    pendingFocusFrames.delete(frame);
    frame = null;
  };
  frame = window.requestAnimationFrame(() => {
    if (frame !== null) pendingFocusFrames.delete(frame);
    frame = null;
    if (generation === focusRestorationGeneration) callback();
  });
  pendingFocusFrames.add(frame);
  return cancel;
}

function dialogFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
}

/** Focus the first control and keep Tab navigation inside an active dialog. */
export function useDialogFocus(
  containerRef: RefObject<HTMLElement | null>,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const focusInitial = () => {
      const target = initialFocusRef?.current ?? dialogFocusableElements(container)[0] ?? container;
      target.focus({ preventScroll: true });
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = dialogFocusableElements(container);
      if (!focusable.length) {
        event.preventDefault();
        container.focus({ preventScroll: true });
        return;
      }
      const active = document.activeElement;
      const currentIndex = focusable.indexOf(active as HTMLElement);
      if (!container.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? focusable[focusable.length - 1] : focusable[0]).focus({ preventScroll: true });
      } else if (event.shiftKey && currentIndex <= 0) {
        event.preventDefault();
        focusable[focusable.length - 1].focus({ preventScroll: true });
      } else if (!event.shiftKey && currentIndex === focusable.length - 1) {
        event.preventDefault();
        focusable[0].focus({ preventScroll: true });
      }
    };

    container.addEventListener("keydown", onKeyDown);
    focusInitial();
    return () => container.removeEventListener("keydown", onKeyDown);
  }, [containerRef, initialFocusRef]);
}
