"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { hydrateUiPreferences, saveUiTheme } from "@/lib/uiPreferences";

type Theme = "dark" | "light";

type ThemeContextValue = {
  theme: Theme;
  setTheme: (next: Theme) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "theme";

// SSR always renders "dark" (the server has no access to the user's
// preference or localStorage). React hydration requires the client's
// first render to match that exactly — so the provider's initial state
// is hard-pinned here. The actual preference is applied post-mount via
// an effect that calls setThemeState; that's a normal state update and
// does not count as a hydration mismatch.
//
// This is the structural fix for React #418: previously the initial
// useState read localStorage / matchMedia / <html data-theme>, which
// returns "light" on the client for users who selected light mode while
// the server still rendered "dark". Any descendant branching on
// `theme` (ClerkThemeBridge, WorkspaceShell's actionTone, kit/page's
// Sun/Moon icon) then produced a different tree during hydration than
// what was sent down from SSR.
const SSR_THEME: Theme = "dark";

function readClientTheme(): Theme {
  if (typeof document === "undefined") return SSR_THEME;
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") return attr;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {}
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

// Apply data-theme only. Do NOT touch <meta name="theme-color"> — those
// tags are owned by Next.js's viewport metadata API and mutating them
// post-mount confuses hydration / re-renders (React #418). PWA chrome
// follows the media-query themeColor pair declared in app/layout.tsx
// (light → #ffffff, dark → #101714) which switches on the OS-level
// prefers-color-scheme; a user override via the in-app toggle does not
// move the PWA status-bar color until next launch. Acceptable tradeoff.
function applyThemeSideEffects(next: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", next);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Pin first render to the SSR default. Reading the real preference here
  // would diverge from the server-rendered HTML and trigger React #418.
  const [theme, setThemeState] = useState<Theme>(SSR_THEME);

  // Post-mount: read the user's actual preference and reconcile state.
  // The pre-paint `ThemeBootstrap` inline script already applied
  // `data-theme` to <html> synchronously so the visible chrome doesn't
  // flash — this effect just brings React's state in line with the DOM.
  useEffect(() => {
    const next = readClientTheme();
    if (next !== theme) setThemeState(next);
    // Intentionally empty deps: this runs once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cross-device sync: the profile row's stored theme fills in when THIS
  // device has no explicit choice yet. An explicit localStorage value stays
  // authoritative locally (it is what ThemeBootstrap painted pre-hydration).
  useEffect(() => {
    let active = true;
    void hydrateUiPreferences().then((prefs) => {
      if (!active || !prefs.theme) return;
      try {
        const explicit = window.localStorage.getItem(STORAGE_KEY);
        if (explicit === "dark" || explicit === "light") return;
        window.localStorage.setItem(STORAGE_KEY, prefs.theme);
      } catch {}
      setThemeState(prefs.theme);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    applyThemeSideEffects(theme);
  }, [theme]);

  useEffect(() => {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      const explicit = window.localStorage.getItem(STORAGE_KEY);
      if (explicit === "dark" || explicit === "light") return;
      setThemeState(event.matches ? "dark" : "light");
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {}
    saveUiTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    return {
      theme: "dark",
      setTheme: () => {},
      toggleTheme: () => {},
    };
  }
  return ctx;
}
