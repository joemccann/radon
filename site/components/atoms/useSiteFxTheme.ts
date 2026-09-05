"use client";

import { useSyncExternalStore } from "react";
import { DEFAULT_SITE_THEME, isSiteTheme, type SiteTheme } from "@/lib/theme";

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

function snapshot(): SiteTheme {
  const attr = document.documentElement.getAttribute("data-theme");
  return isSiteTheme(attr) ? attr : DEFAULT_SITE_THEME;
}

export function useSiteFxTheme(): SiteTheme {
  return useSyncExternalStore(subscribe, snapshot, () => DEFAULT_SITE_THEME);
}
