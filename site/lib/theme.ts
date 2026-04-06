export type SiteTheme = "dark" | "light";

export const DEFAULT_SITE_THEME: SiteTheme = "dark";
export const SITE_THEME_STORAGE_KEY = "theme";
export const siteThemeMetaColor: Record<SiteTheme, string> = {
  dark: "#0a0f14",
  light: "#FFFFFF",
};

export function isSiteTheme(value: unknown): value is SiteTheme {
  return value === "dark" || value === "light";
}

export function resolveInitialTheme(
  savedTheme: string | null | undefined,
  prefersDark: boolean,
): SiteTheme {
  if (isSiteTheme(savedTheme)) {
    return savedTheme;
  }

  return prefersDark ? "dark" : "light";
}

export function resolveSiteTheme(
  value: unknown,
  prefersDark = DEFAULT_SITE_THEME === "dark",
): SiteTheme {
  if (isSiteTheme(value)) {
    return value;
  }

  return prefersDark ? "dark" : "light";
}

export function getNextTheme(theme: SiteTheme): SiteTheme {
  return theme === "dark" ? "light" : "dark";
}

export function toggleSiteTheme(theme: SiteTheme): SiteTheme {
  return getNextTheme(theme);
}

export function siteThemeBootstrapScript(): string {
  return `
    (function () {
      var defaultTheme = ${JSON.stringify(DEFAULT_SITE_THEME)};
      var storageKey = ${JSON.stringify(SITE_THEME_STORAGE_KEY)};
      try {
        var raw = window.localStorage.getItem(storageKey);
        var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        var theme = raw === "light" || raw === "dark"
          ? raw
          : (prefersDark ? "dark" : "light");
        document.documentElement.setAttribute("data-theme", theme);
        var themeMeta = document.querySelector('meta[name="theme-color"]');
        if (themeMeta) {
          themeMeta.setAttribute("content", theme === "light" ? ${JSON.stringify(siteThemeMetaColor.light)} : ${JSON.stringify(siteThemeMetaColor.dark)});
        }
      } catch (error) {
        document.documentElement.setAttribute("data-theme", defaultTheme);
      }
    })();
  `;
}
