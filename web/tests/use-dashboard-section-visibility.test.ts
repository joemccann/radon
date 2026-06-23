/**
 * @vitest-environment jsdom
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  DASHBOARD_SECTION_IDS,
  useDashboardSectionVisibility,
} from "../lib/useDashboardSectionVisibility";

const STORAGE_KEY = "radon:mobile-dashboard:hidden-sections";

function resetLocalStorage(): void {
  if (
    typeof window.localStorage?.clear === "function" &&
    typeof window.localStorage?.getItem === "function" &&
    typeof window.localStorage?.setItem === "function"
  ) {
    window.localStorage.clear();
    return;
  }

  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, String(value)),
      removeItem: (key: string) => store.delete(key),
      clear: () => store.clear(),
    },
  });
}

describe("useDashboardSectionVisibility", () => {
  beforeEach(() => {
    resetLocalStorage();
  });

  it("defaults to nothing hidden", () => {
    const { result } = renderHook(() => useDashboardSectionVisibility());

    expect(result.current.isHidden("portfolio")).toBe(false);
    expect(result.current.isHidden("news")).toBe(false);
  });

  it("hides a section and writes the id to localStorage", async () => {
    const { result } = renderHook(() => useDashboardSectionVisibility());

    act(() => {
      result.current.toggle("portfolio");
    });

    expect(result.current.isHidden("portfolio")).toBe(true);
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual(["portfolio"]);
    });
  });

  it("rehydrates hidden sections from localStorage", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(["portfolio", "news"]));

    const { result } = renderHook(() => useDashboardSectionVisibility());

    await waitFor(() => {
      expect(result.current.isHidden("portfolio")).toBe(true);
      expect(result.current.isHidden("news")).toBe(true);
    });
    expect(result.current.isHidden("orders")).toBe(false);
  });

  it("resets a legacy all-hidden dashboard instead of rendering blank forever", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(DASHBOARD_SECTION_IDS));

    const { result } = renderHook(() => useDashboardSectionVisibility());

    await waitFor(() => {
      for (const id of DASHBOARD_SECTION_IDS) {
        expect(result.current.isHidden(id)).toBe(false);
      }
      expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual([]);
    });
  });

  it("does not allow the final visible section to be hidden", async () => {
    const initiallyHidden = DASHBOARD_SECTION_IDS.filter((id) => id !== "news");
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(initiallyHidden));

    const { result } = renderHook(() => useDashboardSectionVisibility());

    await waitFor(() => {
      expect(result.current.isHidden("portfolio")).toBe(true);
      expect(result.current.isHidden("news")).toBe(false);
    });

    act(() => {
      result.current.toggle("news");
    });

    expect(result.current.isHidden("news")).toBe(false);
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual(initiallyHidden);
    });
  });

  it("ignores malformed and unknown stored values", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(["portfolio", "not-a-section", 42]));

    const { result } = renderHook(() => useDashboardSectionVisibility());

    await waitFor(() => {
      expect(result.current.isHidden("portfolio")).toBe(true);
      expect(result.current.isHidden("not-a-section")).toBe(false);
    });
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual(["portfolio"]);
  });
});
