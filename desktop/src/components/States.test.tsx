import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LoadingState, RuntimeNotReady, SidecarDisconnected } from "./States";

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

  it("uses runtime details when available and a safe default otherwise", () => {
    const { rerender } = render(<RuntimeNotReady />);
    expect(
      screen.getByText("Install the pinned runtime and run a Vulkan health check before indexing."),
    ).toBeInTheDocument();

    rerender(<RuntimeNotReady detail="Vulkan0 is not available in this session." />);
    expect(screen.getByText("Vulkan0 is not available in this session.")).toBeInTheDocument();
  });
});
