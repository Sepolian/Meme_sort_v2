import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LibraryShell } from "./LibraryShell";

describe("LibraryShell", () => {
  it("renders toolbar and scrollable content without an inspector", () => {
    const { container } = render(
      <LibraryShell toolbar={<p>Toolbar</p>} content={<p>Content</p>} />,
    );

    expect(screen.getByRole("toolbar", { name: "Library toolbar" })).toBeInTheDocument();
    expect(screen.getByText("Content")).toBeInTheDocument();
    expect(container.querySelector(".library-body")).toHaveAttribute("data-inspector", "closed");
    expect(container.querySelector(".library-inspector")).toBeNull();
  });

  it("renders a non-overlaying right inspector alongside content when provided", () => {
    const { container } = render(
      <LibraryShell
        toolbar={<p>Toolbar</p>}
        content={<p>Content</p>}
        inspector={<p>Inspector</p>}
      />,
    );

    // Both regions stay mounted simultaneously; inspector is a sibling aside, not a dialog/overlay.
    expect(screen.getByText("Content")).toBeInTheDocument();
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(inspector).toBeInTheDocument();
    expect(inspector.tagName.toLowerCase()).toBe("aside");
    expect(container.querySelector(".library-body")).toHaveAttribute("data-inspector", "open");
    expect(container.querySelector(".library-content + .library-inspector")).not.toBeNull();
  });
});
