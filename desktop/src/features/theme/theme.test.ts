import { expect, it } from "vitest";
import { THEME_PREFERENCE_KEY, getSystemDark, loadThemePreference } from "./theme";

it("falls back to system for invalid stored preferences", () => {
  localStorage.setItem(THEME_PREFERENCE_KEY, "sepia");
  expect(loadThemePreference()).toBe("system");
});

it("keeps theme selection usable when OS appearance is unavailable", () => {
  expect(getSystemDark(() => { throw new Error("unavailable"); })).toBe(false);
});
