// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import TrialExpiredPage from "../app/trial-expired/page";

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("/trial-expired (plate 66)", () => {
  it("keeps the expiry on screen instead of auto-forwarding to the marketing site", () => {
    vi.useFakeTimers();
    const replace = vi.fn();
    const assign = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, replace, assign },
    });
    try {
      render(<TrialExpiredPage />);
      act(() => { vi.advanceTimersByTime(60_000); });
      expect(replace).not.toHaveBeenCalled();
      expect(assign).not.toHaveBeenCalled();
      expect(screen.getByRole("heading", { name: "Your demo has ended" })).toBeTruthy();
      expect(document.body.textContent).not.toMatch(/Redirecting/);
      const link = screen.getByRole("link", { name: "Continue to radon.run" });
      expect(link.getAttribute("href")).toBe("https://radon.run");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original });
    }
  });
});
