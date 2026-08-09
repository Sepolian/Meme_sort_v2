import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("shows the migration status", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: "Tauri migration workspace" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Python sidecar — not connected yet")).toBeInTheDocument();
  });
});
