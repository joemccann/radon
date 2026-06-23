/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDashboardSectionVisibility } from "../lib/useDashboardSectionVisibility";

const STORAGE_KEY = "radon:mobile-dashboard:hidden-sections";

describe("useDashboardSectionVisibility", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to nothing hidden", () => {
    const { result } = renderHook(() => useDashboardSectionVisibility());
    expect(result.current.isHidden("portfolio")).toBe(false);
  });

  it("hides a section and writes the id to localStorage", () => {
    const { result } = renderHook(() => useDashboardSectionVisibility());

    act(() => {
      result.current.toggle("portfolio");
    });

    expect(result.current.isHidden("portfolio")).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual(["portfolio"]);
  });

  it("toggling a hidden section shows it again and clears it from storage", () => {
    const { result } = renderHook(() => useDashboardSectionVisibility());

    act(() => {
      result.current.toggle("portfolio");
    });
    act(() => {
      result.current.toggle("portfolio");
    });

    expect(result.current.isHidden("portfolio")).toBe(false);
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual([]);
  });

  it("rehydrates hidden sections from localStorage on a fresh mount", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(["portfolio"]));

    const { result } = renderHook(() => useDashboardSectionVisibility());

    expect(result.current.isHidden("portfolio")).toBe(true);
    expect(result.current.isHidden("orders")).toBe(false);
  });

  it("ignores malformed stored values", () => {
    window.localStorage.setItem(STORAGE_KEY, "not json");

    const { result } = renderHook(() => useDashboardSectionVisibility());

    expect(result.current.isHidden("portfolio")).toBe(false);
  });
});
