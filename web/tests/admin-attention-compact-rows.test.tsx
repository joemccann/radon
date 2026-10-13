/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import AdminAttentionQueue from "../components/admin/AdminAttentionQueue";
import type { AdminAttentionCondition } from "../lib/adminAttention";

afterEach(cleanup);
const NOW = Date.parse("2026-09-25T18:00:00Z");
const condition = (id: string, detail: string): AdminAttentionCondition => ({
  id, action: "writers", title: `${id}: update failed`, detail, tone: "warning", source: "telemetry", observedAt: new Date(NOW - 120_000).toISOString(),
} as AdminAttentionCondition);

describe("admin attention queue rows (plate 21)", () => {
  it("keeps the explanation on the primary condition only; secondary conditions are one row", () => {
    const onReview = vi.fn();
    render(
      <AdminAttentionQueue
        conditions={[condition("first", "Primary explanation."), condition("second", "Secondary explanation.")]}
        now={NOW}
        onReview={onReview}
      />,
    );
    const primary = screen.getByTestId("admin-attention-first");
    expect(primary.textContent).toContain("Primary explanation.");
    const secondary = screen.getByTestId("admin-attention-second");
    expect(secondary.textContent).not.toContain("Secondary explanation.");
    expect(secondary.querySelector("p")).toBeNull();
    expect(within(secondary).getByRole("heading", { name: "second: update failed" }).getAttribute("title")).toBe("Secondary explanation.");
    expect(secondary.textContent).toContain("Observed 2m ago");
    fireEvent.click(within(secondary).getByRole("button", { name: "Review writers" }));
    expect(onReview).toHaveBeenCalledWith("writers");
  });
});
