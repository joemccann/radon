"use client";

import { Moon, Sun } from "lucide-react";
import { useCallback, useSyncExternalStore } from "react";
import {
  DEFAULT_SITE_THEME,
  isSiteTheme,
  SITE_THEME_STORAGE_KEY,
  siteThemeMetaColor,
  getNextTheme,
  type SiteTheme,
} from "@/lib/theme";

// The theme lives on <html data-theme> (applied pre-hydration by the bootstrap
// script in layout.tsx). We read it through useSyncExternalStore so SSR and the
// first client render agree on the default, then React reconciles to the real
// client value without a setState-in-effect or a hydration mismatch.
const themeListeners = new Set<() => void>();

function subscribeTheme(onChange: () => void) {
  themeListeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    themeListeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getThemeSnapshot(): SiteTheme {
  const attr = document.documentElement.getAttribute("data-theme");
  return isSiteTheme(attr) ? attr : DEFAULT_SITE_THEME;
}

function getServerThemeSnapshot(): SiteTheme {
  return DEFAULT_SITE_THEME;
}

function applySiteTheme(theme: SiteTheme) {
  document.documentElement.setAttribute("data-theme", theme);
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) {
    themeMeta.setAttribute("content", siteThemeMetaColor[theme]);
  }
  themeListeners.forEach((listener) => listener());
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(
    subscribeTheme,
    getThemeSnapshot,
    getServerThemeSnapshot,
  );

  const nextTheme = getNextTheme(theme);
  const label = nextTheme === "light" ? "Light" : "Dark";
  const ariaLabel = `Switch to ${nextTheme} mode`;

  const handleToggle = useCallback(() => {
    const upcomingTheme = getNextTheme(getThemeSnapshot());
    applySiteTheme(upcomingTheme);
    try {
      window.localStorage.setItem(SITE_THEME_STORAGE_KEY, upcomingTheme);
    } catch {
      // localStorage unavailable (private mode); theme still applies for the session.
    }
  }, []);

  return (
    <button
      type="button"
      data-testid="site-theme-toggle"
      suppressHydrationWarning
      aria-label={ariaLabel}
      title={ariaLabel}
      className="inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-[8px] border border-grid bg-panel px-3 text-[12px] font-medium text-primary transition-colors hover:bg-panel-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60"
      onClick={handleToggle}
    >
      {nextTheme === "light" ? <Sun size={14} /> : <Moon size={14} />}
      <span suppressHydrationWarning>{label}</span>
    </button>
  );
}
