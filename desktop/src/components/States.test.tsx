import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LoadingState, SidecarDisconnected } from "./States";

describe("desktop state components", () => {
  it("announces that the authenticated sidecar is connecting", () => {
    render(<LoadingState />);

    expect(screen.getByRole("main")).toHaveAttribute("aria-live", "polite");
    expect(
      screen.getByRole("heading", { name: "Connecting to the authenticated sidecar" }),
    ).toBeInTheDocument();
  });

  it("offers a retry action when the sidecar is disconnected", () => {
    const onRetry = vi.fn();
    render(<SidecarDisconnected onRetry={onRetry} />);

    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));

    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByRole("main")).toHaveAttribute("aria-live", "assertive");
  });

});
