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

    fireEvent.mouseDown(screen.getByLabelText("Window drag region"), { button: 0 });
    fireEvent.doubleClick(screen.getByLabelText("Window drag region"));

    expect(windowControls.startDragging).toHaveBeenCalledOnce();
    expect(windowControls.toggleMaximize).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Restore window" })).toBeInTheDocument();
  });

  it("renders inert controls for browser preview", () => {
    const windowControls = controls({ isNative: false });
    render(<TitleBar controls={windowControls} />);

    expect(screen.getByRole("button", { name: "Minimize window" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Maximize window" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close window" })).toBeDisabled();
  });
});
