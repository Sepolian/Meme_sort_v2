import { useCallback, useState } from "react";

export interface AssetContextMenuAnchor {
  x: number;
  y: number;
}

/**
 * Right-click anchor state for Asset media (ticket 01 follow-up).
 *
 * Opening suppresses the WebView-native image menu ("Copy image" copies the
 * rendered static bitmap, which silently flattens GIF animation). Menus
 * built on this anchor route through the native `MemeSortClient` commands
 * (ID-only, never paths) instead.
 */
export function useAssetContextMenu() {
  const [anchor, setAnchor] = useState<AssetContextMenuAnchor | null>(null);
  const openMenu = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    setAnchor({ x: event.clientX, y: event.clientY });
  }, []);
  const closeMenu = useCallback(() => {
    setAnchor(null);
  }, []);
  return { anchor, openMenu, closeMenu };
}
