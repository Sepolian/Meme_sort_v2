import { useCallback, useEffect, useState } from "react";

export interface AssetContextMenuAnchor {
  x: number;
  y: number;
  opener: HTMLElement | null;
}

/**
 * Right-click anchor state for Asset media (ticket 01 follow-up).
 *
 * Opening suppresses the WebView-native image menu ("Copy image" copies the
 * rendered static bitmap, which silently flattens GIF animation). Menus
 * built on this anchor route through the native `MemeSortClient` commands
 * (ID-only, never paths) instead.
 */
export function useAssetContextMenu(disabled = false) {
  const [anchor, setAnchor] = useState<AssetContextMenuAnchor | null>(null);
  const openMenu = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    if (disabled) {
      setAnchor(null);
      return;
    }
    const target = event.currentTarget;
    const opener =
      target.closest<HTMLElement>(".asset-card")?.querySelector<HTMLElement>(".asset-card-open") ??
      target.closest<HTMLElement>(".library-inspector")?.querySelector<HTMLElement>(".inspector-header button") ??
      (document.activeElement instanceof HTMLElement && document.activeElement !== document.body
        ? document.activeElement
        : null);
    setAnchor({ x: event.clientX, y: event.clientY, opener });
  }, [disabled]);
  const closeMenu = useCallback(() => {
    setAnchor(null);
  }, []);
  useEffect(() => {
    if (disabled) closeMenu();
  }, [closeMenu, disabled]);
  return { anchor, openMenu, closeMenu };
}
