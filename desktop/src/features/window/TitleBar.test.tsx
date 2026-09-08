import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TitleBar } from "./TitleBar";
import type { WindowControls } from "./window-controls";

function controls(overrides: Partial<WindowControls> = {}): WindowControls {
  return {
    isNative: true,
    isMaximized: false,
    minimize: vi.fn(async () => true),
    toggleMaximize: vi.fn(async () => true),
    close: vi.fn(async () => true),
    startDragging: vi.fn(async () => true),
    ...overrides,
  };
}

describe("TitleBar", () => {
  it("provides accessible window controls without making the buttons draggable", () => {
    const windowControls = controls();
    render(<TitleBar controls={windowControls} />);

    fireEvent.mouseDown(screen.getByRole("button", { name: "Minimize window" }));
    fireEvent.click(screen.getByRole("button", { name: "Minimize window" }));
    fireEvent.click(screen.getByRole("button", { name: "Maximize window" }));
    fireEvent.click(screen.getByRole("button", { name: "Close window" }));

    expect(windowControls.startDragging).not.toHaveBeenCalled();
    expect(windowControls.minimize).toHaveBeenCalledOnce();
    expect(windowControls.toggleMaximize).toHaveBeenCalledOnce();
    expect(windowControls.close).toHaveBeenCalledOnce();
  });

  it("uses the dedicated drag region and reflects a restored window", () => {
    const windowControls = controls({ isMaximized: true });
    render(<TitleBar controls={windowControls} />);
    const dragRegion = screen.getByLabelText("Window drag region");

    fireEvent.mouseDown(dragRegion, { button: 0 });
    fireEvent.doubleClick(dragRegion);

    expect(dragRegion).toHaveAttribute("data-tauri-drag-region", "deep");
    expect(windowControls.startDragging).not.toHaveBeenCalled();
    expect(windowControls.toggleMaximize).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Restore window" })).toBeInTheDocument();
  });

  it("renders inert controls for browser preview", () => {
    const windowControls = controls({ isNative: false });
    render(<TitleBar controls={windowControls} />);

    expect(screen.getByRole("button", { name: "Minimize window" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Maximize window" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close window" })).toBeDisabled();
  });

  it("renders the packaged MemeSort artwork at the title bar icon size", () => {
    const { container } = render(<TitleBar controls={controls()} />);
    const brandMark = container.querySelector(".titlebar-brand-mark");

    expect(brandMark?.tagName).toBe("IMG");
    expect(brandMark).toHaveAttribute("src");
    expect(brandMark).toHaveAttribute("width", "22");
    expect(brandMark).toHaveAttribute("height", "22");
    expect(brandMark).toHaveAttribute("draggable", "false");
  });
});
