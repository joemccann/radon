/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useViewport } from "../lib/useViewport";
import { classifyViewport, BREAKPOINTS } from "../lib/breakpoints";

function setWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
}

function dispatchResize(): void {
  act(() => {
    window.dispatchEvent(new Event("resize"));
  });
}

describe("classifyViewport", () => {
  it("returns mobile for widths at or below the mobile breakpoint", () => {
    expect(classifyViewport(0)).toBe("mobile");
    expect(classifyViewport(393)).toBe("mobile");
    expect(classifyViewport(BREAKPOINTS.mobile)).toBe("mobile");
  });

  it("returns tablet for widths above mobile and below desktop", () => {
    expect(classifyViewport(BREAKPOINTS.mobile + 1)).toBe("tablet");
    expect(classifyViewport(820)).toBe("tablet");
    expect(classifyViewport(BREAKPOINTS.tablet - 1)).toBe("tablet");
  });

  it("returns desktop at and above the tablet breakpoint", () => {
    expect(classifyViewport(BREAKPOINTS.tablet)).toBe("desktop");
    expect(classifyViewport(1440)).toBe("desktop");
    expect(classifyViewport(2560)).toBe("desktop");
  });
});

describe("useViewport", () => {
  beforeEach(() => {
    setWidth(1280);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns desktop and hasMounted=false on initial SSR-style render then settles to current width on mount", () => {
    setWidth(393);
    const { result } = renderHook(() => useViewport());

    expect(result.current.viewportClass).toBe("mobile");
    expect(result.current.isMobile).toBe(true);
    expect(result.current.isTablet).toBe(false);
    expect(result.current.isDesktop).toBe(false);
    expect(result.current.width).toBe(393);
    expect(result.current.hasMounted).toBe(true);
  });

  it("classifies an iPhone 16 viewport as mobile", () => {
    setWidth(393);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isMobile).toBe(true);
    expect(result.current.viewportClass).toBe("mobile");
  });

  it("classifies an iPad-style viewport as tablet", () => {
    setWidth(820);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isTablet).toBe(true);
    expect(result.current.viewportClass).toBe("tablet");
  });

  it("classifies a laptop viewport as desktop", () => {
    setWidth(1440);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isDesktop).toBe(true);
    expect(result.current.viewportClass).toBe("desktop");
  });

  it("re-classifies on window resize from desktop to mobile", () => {
    setWidth(1440);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isDesktop).toBe(true);

    setWidth(393);
    dispatchResize();

    expect(result.current.isMobile).toBe(true);
    expect(result.current.width).toBe(393);
  });

  it("re-classifies on orientation change from mobile portrait to landscape tablet width", () => {
    setWidth(393);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isMobile).toBe(true);

    setWidth(844);
    act(() => {
      window.dispatchEvent(new Event("orientationchange"));
    });

    expect(result.current.isTablet).toBe(true);
    expect(result.current.width).toBe(844);
  });
});
