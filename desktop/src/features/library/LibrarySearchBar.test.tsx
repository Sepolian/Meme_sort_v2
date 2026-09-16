import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LibrarySearchBar } from "./LibrarySearchBar";

function renderSearchBar(
  onQueryChange = vi.fn(),
  onSubmit = vi.fn(),
  semanticBlocked = false,
) {
  render(
    <LibrarySearchBar
      query=""
      isSearching={false}
      semanticBlocked={semanticBlocked}
      onQueryChange={onQueryChange}
      onSubmit={onSubmit}
      onClear={vi.fn()}
    />,
  );
  return {
    input: screen.getByLabelText("Search Library"),
    mode: screen.getByLabelText("Search mode"),
    onQueryChange,
    onSubmit,
  };
}

describe("LibrarySearchBar", () => {
  it("defaults to By meaning and keeps typing in the draft", () => {
    const { input, mode, onQueryChange, onSubmit } = renderSearchBar();

    expect(mode).toHaveValue("meaning");
    fireEvent.change(input, { target: { value: "a joyful reaction" } });

    expect(input).toHaveValue("a joyful reaction");
    expect(onQueryChange).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Search" })).not.toBeDisabled();
    fireEvent.submit(input.closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith("a joyful reaction");
    expect(input).toHaveAttribute("placeholder", "Describe the reaction to search by meaning");
  });

  it("keeps IME composition local and commits the final value once", () => {
    const { input, mode, onQueryChange } = renderSearchBar();

    fireEvent.change(mode, { target: { value: "filename" } });

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

  it("explains blocked semantic work according to the selected mode", () => {
    const { mode } = renderSearchBar(vi.fn(), vi.fn(), true);

    expect(screen.getByRole("note")).toHaveTextContent(
      "By meaning keeps your draft local without filtering the Library.",
    );
    fireEvent.change(mode, { target: { value: "filename" } });
    expect(screen.getByRole("note")).toHaveTextContent(
      "By filename still filters the loaded Library locally.",
    );
  });

  it("exposes the image picker as Search by image", () => {
    const onImageSearch = vi.fn();
    render(
      <LibrarySearchBar
        query=""
        isSearching={false}
        onQueryChange={vi.fn()}
        onSubmit={vi.fn()}
        onClear={vi.fn()}
        onImageSearch={onImageSearch}
      />,
    );

    const action = screen.getByRole("button", { name: "Search by image" });
    expect(action).toHaveTextContent("Search by image");
    fireEvent.click(action);
    expect(onImageSearch).toHaveBeenCalledOnce();
  });
});
