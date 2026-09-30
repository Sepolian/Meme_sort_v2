import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open confirmation</button>
      <button type="button">Other control</button>
      {open ? (
        <ConfirmDialog
          titleId="confirm-test-title"
          title="Confirm test"
          detail="Test detail"
          confirmLabel="Confirm"
          pending={false}
          onCancel={() => setOpen(false)}
          onConfirm={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

async function nextAnimationFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  });
}

describe("ConfirmDialog focus ownership", () => {
  it("does not steal focus after cancellation when the user focuses elsewhere", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open confirmation" });
    opener.focus();
    fireEvent.click(opener);

    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    const other = screen.getByRole("button", { name: "Other control" });
    other.focus();
    await nextAnimationFrame();

    expect(other).toHaveFocus();
  });

  it("cancels an old restoration when a new confirmation owns the surface", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open confirmation" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    fireEvent.click(opener);
    const confirm = await screen.findByRole("button", { name: "Confirm" });
    await nextAnimationFrame();

    expect(confirm).toHaveFocus();
  });
});
