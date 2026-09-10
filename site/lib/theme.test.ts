import { describe, expect, it } from "vitest";
import {
  DEFAULT_SITE_THEME,
  SITE_THEME_STORAGE_KEY,
  getNextTheme,
  isSiteTheme,
  resolveInitialTheme,
  resolveSiteTheme,
  siteThemeMetaColor,
} from "./theme";

describe("site theme helpers", () => {
  it("accepts only dark and light theme ids", () => {
    expect(isSiteTheme("dark")).toBe(true);
    expect(isSiteTheme("light")).toBe(true);
    expect(isSiteTheme("system")).toBe(false);
    expect(isSiteTheme(undefined)).toBe(false);
  });

  it("resolves invalid values with a system-aware fallback", () => {
    expect(DEFAULT_SITE_THEME).toBe("light");
    expect(SITE_THEME_STORAGE_KEY).toBe("theme");
    expect(resolveSiteTheme("light")).toBe("light");
    expect(resolveSiteTheme("dark")).toBe("dark");
    expect(resolveSiteTheme("")).toBe("light");
    expect(resolveSiteTheme("system")).toBe("light");
    expect(resolveSiteTheme(null)).toBe("light");
    expect(resolveSiteTheme("", true)).toBe("dark");
  });

  it("respects an explicit saved theme before system preference", () => {
    expect(resolveInitialTheme("light", true)).toBe("light");
    expect(resolveInitialTheme("dark", false)).toBe("dark");
  });

  it("falls back to the system preference when no saved theme exists", () => {
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme(null, false)).toBe("light");
  });

  it("toggles between dark and light and exposes browser theme colors", () => {
    expect(getNextTheme("dark")).toBe("light");
    expect(getNextTheme("light")).toBe("dark");
    expect(siteThemeMetaColor.dark).toBe("#101714");
    expect(siteThemeMetaColor.light).toBe("#ffffff");
  });
});
