import { act, render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { MemoryRouter, useNavigate, useSearchParams } from "react-router-dom";
import { LIBRARY_PREFERENCE_KEYS } from "./libraryUrlState";
import { useLibraryUrlState } from "./useLibraryUrlState";

function Probe() {
  const state = useLibraryUrlState();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="state">{`${state.sort}|${state.media}|${state.assetId ?? ""}`}</output>
      <output aria-label="URL">{params.toString()}</output>
      <button onClick={() => state.setMedia("gif")}>set media</button>
      <button onClick={state.clearQuery}>clear query</button>
      <button onClick={state.clearAssetId}>clear asset</button>
      <button onClick={() => navigate(-1)}>back</button>
    </>
  );
}

function renderAt(...routes: string[]) {
  render(<MemoryRouter initialEntries={routes} initialIndex={routes.length - 1}><Probe /></MemoryRouter>);
}

beforeEach(() => localStorage.clear());

it("normalizes invalid URL values and falls back to storage", () => {
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "oldest");
  renderAt("/?sort=bogus&media=gif&asset=asset-1");
  expect(screen.getByLabelText("state")).toHaveTextContent("oldest|gif|asset-1");
  expect(screen.getByLabelText("URL")).toHaveTextContent("media=gif");
  expect(screen.getByLabelText("URL")).not.toHaveTextContent("bogus");
});

it("prefers a valid URL value over a stored preference", () => {
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "oldest");
  renderAt("/?sort=name");
  expect(screen.getByLabelText("state")).toHaveTextContent("name|all|");
});

it("does not add history when a filter is already active", () => {
  renderAt("/?q=cat", "/?q=cat&media=gif");
  act(() => screen.getByRole("button", { name: "set media" }).click());
  act(() => screen.getByRole("button", { name: "back" }).click());
  expect(screen.getByLabelText("URL")).toHaveTextContent("q=cat");
  expect(screen.getByLabelText("URL")).not.toHaveTextContent("media=gif");
});

it("clears an explicitly empty query parameter", () => {
  renderAt("/?q=");
  act(() => screen.getByRole("button", { name: "clear query" }).click());
  expect(screen.getByLabelText("URL")).toBeEmptyDOMElement();
});

it("does not add history when clearing an absent query", () => {
  renderAt("/?q=cat", "/?sort=oldest");
  act(() => screen.getByRole("button", { name: "clear query" }).click());
  act(() => screen.getByRole("button", { name: "back" }).click());
  expect(screen.getByLabelText("URL")).toHaveTextContent("q=cat");
});

it("clears only the inspector target", () => {
  renderAt("/?q=cat&sort=oldest&asset=asset-1");
  act(() => screen.getByRole("button", { name: "clear asset" }).click());
  expect(screen.getByLabelText("URL")).toHaveTextContent("q=cat&sort=oldest");
  expect(screen.getByLabelText("URL")).not.toHaveTextContent("asset=");
});
