import { useEffect, useRef } from "react";

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
