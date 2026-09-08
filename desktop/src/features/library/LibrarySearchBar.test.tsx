import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LibrarySearchBar } from "./LibrarySearchBar";

function renderSearchBar(onQueryChange = vi.fn()) {
  render(
    <LibrarySearchBar
      query=""
      isSearching={false}
      onQueryChange={onQueryChange}
      onSubmit={vi.fn()}
      onClear={vi.fn()}
    />,
  );
  return {
    input: screen.getByLabelText("Search Library"),
    onQueryChange,
  };
}

describe("LibrarySearchBar", () => {
  it("keeps IME composition local and commits the final value once", () => {
    const { input, onQueryChange } = renderSearchBar();

    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "d" } });
    fireEvent.change(input, { target: { value: "da" } });

    expect(input).toHaveValue("da");
    expect(onQueryChange).not.toHaveBeenCalled();

    fireEvent.compositionEnd(input, { data: "da" });
    fireEvent.change(input, { target: { value: "da" } });

    expect(input).toHaveValue("da");
    expect(onQueryChange).toHaveBeenCalledOnce();
    expect(onQueryChange).toHaveBeenCalledWith("da");
  });
});
