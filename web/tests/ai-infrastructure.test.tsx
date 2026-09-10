// @vitest-environment jsdom
import React from "react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { AiInfrastructureView } from "@/components/AiInfrastructurePanel";
import { aiNumber, historyGroups, sourceHref } from "@/lib/aiInfrastructure";
import { fetchAiInfrastructure } from "@/lib/useAiInfrastructure";
import { aiFixture } from "./fixtures/aiInfrastructure";
vi.mock("@/components/LlmTokenIndexCard", () => ({ default: () => <div>Legacy chart fixture</div> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("AI infrastructure evidence", () => {
  it("keeps four panes and keyboard navigation", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Demand" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Compute" }).getAttribute("aria-selected")).toBe("true");
    for (const name of ["Delivery", "Finance", "Demand"]) { fireEvent.click(screen.getByRole("tab", { name })); expect(screen.getByRole("tab", { name }).getAttribute("aria-selected")).toBe("true"); }
  });
  it("never turns no data or transport error into clear", () => {
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [], shadow: { ...aiFixture.shadow, state: "clear" } }} error="HTTP 503" loading={false} refresh={() => {}} />);
    expect(screen.getByText("Insufficient evidence")).toBeTruthy(); expect(screen.getByRole("alert").textContent).toContain("HTTP 503"); expect(screen.getByText(/No verified observations in this view/)).toBeTruthy();
  });
  it("keeps stale measurement state visible", () => {
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [{ ...aiFixture.indicators[0], status: "stale" }] }} error={null} loading={false} refresh={() => {}} />); expect(screen.getByText("stale")).toBeTruthy();
  });
  it("labels every indicator with linked provider status and observed coverage", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    const indicator = screen.getByTestId("ai-indicator-D1");
    const source = indicator.querySelector('[data-testid="ai-source-fixture"]');
    expect(source?.textContent).toContain("Fixture publisher");
    expect(source?.textContent).toContain("available");
    expect(source?.textContent).toContain("Sep 1, 2026");
    expect(source?.querySelector("a")?.getAttribute("href")).toBe("https://example.com/evidence");
    expect(screen.getByTestId("ai-indicator-D5")).toBeTruthy();
    expect(screen.getByTestId("ai-indicator-D5").textContent).toContain("Ramp business AI spend");
  });
  it("keeps a page-level coverage board for every snapshot source on every pane", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    const board = screen.getByTestId("ai-source-coverage");
    expect(board.querySelector('[data-testid="ai-coverage-fixture"]')?.textContent).toContain("Fixture publisher");
    expect(board.querySelector('[data-testid="ai-coverage-ramp"]')?.textContent).toContain("Ramp AI Index");
    fireEvent.click(screen.getByRole("tab", { name: "Finance" }));
    expect(screen.getByTestId("ai-source-coverage").querySelector('[data-testid="ai-coverage-ramp"]')?.textContent).toContain("128");
  });
  it("groups histories by unit and series, preserves chronological order, rejects invalid points", () => {
    const p = aiFixture.indicators[0].history[0]; const result = historyGroups([{ ...p, date: "2026-09-02" }, { ...p, date: "2026-09-01" }, { ...p, unit: "requests" }, { ...p, value: NaN }]);
    expect(result).toHaveLength(2); expect(result[0][0].date).toBe("2026-09-01"); expect(aiNumber(null)).toBe("Unavailable"); expect(aiNumber(0)).toBe("0");
  });
  it("rejects unsafe or credential-bearing source links", () => { expect(sourceHref("javascript:alert(1)")).toBeUndefined(); expect(sourceHref("https://user:secret@example.com")).toBeUndefined(); expect(sourceHref("https://example.com/source")).toBe("https://example.com/source"); });
  it("fetches uncached and rejects transport/schema failure", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 503 }).mockResolvedValueOnce({ ok: true, json: async () => ({ version: 2 }) }).mockResolvedValueOnce({ ok: true, json: async () => aiFixture }); vi.stubGlobal("fetch", fetch);
    await expect(fetchAiInfrastructure("/api/ai-cycle")).rejects.toThrow("HTTP 503"); await expect(fetchAiInfrastructure("/api/ai-cycle")).rejects.toThrow("incompatible"); await expect(fetchAiInfrastructure("/api/ai-cycle")).resolves.toEqual(aiFixture); expect(fetch).toHaveBeenCalledWith("/api/ai-cycle", { cache: "no-store" });
  });
});
