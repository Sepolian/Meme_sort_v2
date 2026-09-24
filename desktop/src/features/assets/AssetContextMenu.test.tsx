import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetWaterfall } from "./AssetWaterfall";
import type { AssetSummary } from "../../api/types";

function makeGif(asset_id: string): AssetSummary {
  return {
    asset_id,
    library_path: `originals/${asset_id}.gif`,
    library_url: `/media/originals/${asset_id}.gif`,
    thumbnail_url: `/media/thumbnails/${asset_id}.jpg`,
    media_type: "image/gif",
    content_hash: `hash-${asset_id}`,
    width: 208,
    height: 112,
    imported_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: `C:/Source/${asset_id}.gif` }],
    status: "indexed",
  };
}

const GIF_ID = "123e4567-e89b-12d3-a456-426614174010";

function rightClick(target: Element): boolean {
  // Returns false when the native menu was suppressed (defaultPrevented).
  return fireEvent.contextMenu(target, { button: 2, clientX: 120, clientY: 90 });
}

describe("Asset right-click copy menu (ticket 01 follow-up)", () => {
  it("suppresses the native image menu and offers Copy image plus Copy original file", async () => {
    const onCopyImage = vi.fn();
    const onCopyOriginal = vi.fn();
    const { container } = render(
      <AssetWaterfall
        assets={[makeGif(GIF_ID)]}
        density="comfortable"
        checkedIds={new Set()}
        onOpenAsset={() => undefined}
        onToggleChecked={() => undefined}
        onCopyImage={onCopyImage}
        onCopyOriginal={onCopyOriginal}
        columnCount={1}
      />,
    );
    const card = container.querySelector(
      `article[data-asset-id="${GIF_ID}"]`,
    ) as HTMLElement;
    const opener = screen.getByRole("button", { name: `Open ${GIF_ID}.gif` });
    opener.focus();

    expect(rightClick(card)).toBe(false);

    const menu = screen.getByRole("menu", { name: `Actions for ${GIF_ID}.gif` });
    expect(menu).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy image" }));
    expect(onCopyImage).toHaveBeenCalledTimes(1);
    expect(onCopyImage).toHaveBeenCalledWith(GIF_ID);
    expect(onCopyOriginal).not.toHaveBeenCalled();
    // Selecting an item dismisses the menu.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("suppresses pointer and keyboard context menus while both copy actions are busy", () => {
    const onCopyImage = vi.fn();
    const onCopyOriginal = vi.fn();
    const { container } = render(
      <AssetWaterfall
        assets={[makeGif(GIF_ID)]}
        density="comfortable"
        checkedIds={new Set()}
        onOpenAsset={() => undefined}
        onToggleChecked={() => undefined}
        onCopyImage={onCopyImage}
        onCopyOriginal={onCopyOriginal}
        copyBusy
        columnCount={1}
      />,
    );
    const card = container.querySelector(
      `article[data-asset-id="${GIF_ID}"]`,
    ) as HTMLElement;

    expect(rightClick(card)).toBe(false);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    const opener = screen.getByRole("button", { name: `Open ${GIF_ID}.gif` });
    opener.focus();
    expect(fireEvent.contextMenu(opener, { button: 0, detail: 0 })).toBe(false);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(onCopyImage).not.toHaveBeenCalled();
    expect(onCopyOriginal).not.toHaveBeenCalled();
  });

  it("closes the menu on Escape", () => {
    const { container } = render(
      <AssetWaterfall
        assets={[makeGif(GIF_ID)]}
        density="comfortable"
        checkedIds={new Set()}
        onOpenAsset={() => undefined}
        onToggleChecked={() => undefined}
        onCopyImage={() => undefined}
        onCopyOriginal={() => undefined}
        columnCount={1}
      />,
    );
    const card = container.querySelector(
      `article[data-asset-id="${GIF_ID}"]`,
    ) as HTMLElement;

    expect(rightClick(card)).toBe(false);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does not steal focus after dismissal when the user focuses another control", async () => {
    const { container } = render(
      <div>
        <button type="button">Other control</button>
        <AssetWaterfall
          assets={[makeGif(GIF_ID)]}
          density="comfortable"
          checkedIds={new Set()}
          onOpenAsset={() => undefined}
          onToggleChecked={() => undefined}
          onCopyImage={() => undefined}
          onCopyOriginal={() => undefined}
          columnCount={1}
        />
      </div>,
    );
    const card = container.querySelector(`article[data-asset-id="${GIF_ID}"]`) as HTMLElement;
    expect(rightClick(card)).toBe(false);
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy image" }));

    const other = screen.getByRole("button", { name: "Other control" });
    other.focus();
    await act(async () => {
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    });
    expect(other).toHaveFocus();
  });

  it("cancels a dismissed menu restoration when another menu opens before the next frame", async () => {
    const { container } = render(
      <AssetWaterfall
        assets={[makeGif(GIF_ID)]}
        density="comfortable"
        checkedIds={new Set()}
        onOpenAsset={() => undefined}
        onToggleChecked={() => undefined}
        onCopyImage={() => undefined}
        onCopyOriginal={() => undefined}
        columnCount={1}
      />,
    );
    const card = container.querySelector(`article[data-asset-id="${GIF_ID}"]`) as HTMLElement;
    expect(rightClick(card)).toBe(false);
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy image" }));
    expect(rightClick(card)).toBe(false);

    const menuItem = screen.getByRole("menuitem", { name: "Copy image" });
    await act(async () => {
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    });
    expect(menuItem).toHaveFocus();
  });
});
