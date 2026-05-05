"use client";

import { useEffect, useState } from "react";
import { BREAKPOINTS, classifyViewport, type ViewportClass } from "./breakpoints";

export type Viewport = {
  viewportClass: ViewportClass;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  width: number;
  hasMounted: boolean;
};

const DESKTOP_FALLBACK_WIDTH = BREAKPOINTS.tablet;

function readWidth(): number {
  if (typeof window === "undefined") return DESKTOP_FALLBACK_WIDTH;
  return window.innerWidth;
}

export function useViewport(): Viewport {
  const [width, setWidth] = useState<number>(DESKTOP_FALLBACK_WIDTH);
  const [hasMounted, setHasMounted] = useState(false);

  useEffect(() => {
    setHasMounted(true);
    setWidth(readWidth());

    const handleResize = () => setWidth(readWidth());
    window.addEventListener("resize", handleResize, { passive: true });
    window.addEventListener("orientationchange", handleResize, { passive: true });
    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("orientationchange", handleResize);
    };
  }, []);

  const viewportClass = classifyViewport(width);
  return {
    viewportClass,
    isMobile: viewportClass === "mobile",
    isTablet: viewportClass === "tablet",
    isDesktop: viewportClass === "desktop",
    width,
    hasMounted,
  };
}
