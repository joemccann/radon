// @vitest-environment jsdom
import React from "react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { AiInfrastructureView } from "@/components/AiInfrastructurePanel";
import { aiNumber, historyGroups, sourceHref } from "@/lib/aiInfrastructure";
import { fetchAiInfrastructure } from "@/lib/useAiInfrastructure";
import { aiFixture } from "./fixtures/aiInfrastructure";
vi.mock("@/components/AiIndustryHistoryChart", () => ({ default: () => <div>History chart</div> }));
vi.mock("@/components/LlmTokenIndexCard", () => ({ default: () => <div>Legacy chart fixture</div> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("AI infrastructure evidence", () => {
  it("keeps four value-chain stages and keyboard navigation", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: /Adoption/ }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: /Compute/ }).getAttribute("aria-selected")).toBe("true");
    for (const name of ["Buildout", "Funding", "Adoption"]) { fireEvent.click(screen.getByRole("tab", { name: new RegExp(name) })); expect(screen.getByRole("tab", { name: new RegExp(name) }).getAttribute("aria-selected")).toBe("true"); }
  });
  it("never turns no data or transport error into clear", () => {
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [], shadow: { ...aiFixture.shadow, state: "clear" } }} error="HTTP 503" loading={false} refresh={() => {}} />);
    expect(screen.getByText(/Experimental research state · Insufficient evidence/)).toBeTruthy(); expect(screen.getByRole("alert").textContent).toContain("temporarily unavailable"); expect(screen.getByText(/No verified observations in this view/)).toBeTruthy();
  });
  it("keeps stale measurement state visible", () => {
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [{ ...aiFixture.indicators[0], status: "stale" }] }} error={null} loading={false} refresh={() => {}} />); expect(screen.getAllByText(/stale/).length).toBeGreaterThan(0);
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
    expect(screen.getByTestId("ai-indicator-D5").textContent).toContain("Business AI spending");
    fireEvent.click(screen.getByRole("tab", { name: /Compute/ }));
    const thirdVenue = screen.getByTestId("ai-indicator-C5");
    expect(thirdVenue.textContent).toContain("Liquid Compute GPU index");
    expect(thirdVenue.textContent).toContain("Third venue versus the rental book");
    expect(thirdVenue.textContent).toContain("opaque until licensed");
    expect(thirdVenue.querySelector('[data-testid="ai-source-liquidcompute"]')?.textContent).toContain("Liquid Compute GPU index");
  });
  it("keeps a page-level coverage board for every snapshot source on every pane", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    const board = screen.getByTestId("ai-source-coverage");
    expect(board.querySelector('[data-testid="ai-coverage-fixture"]')?.textContent).toContain("Fixture publisher");
    expect(board.querySelector('[data-testid="ai-coverage-ramp"]')?.textContent).toContain("Ramp AI Index");
    expect(board.querySelector('[data-testid="ai-coverage-liquidcompute"]')?.textContent).toContain("Liquid Compute GPU index");
    fireEvent.click(screen.getByRole("tab", { name: /Funding/ }));
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

describe("AI Industry value-chain presentation", () => {
  it("keeps every configured measure available in independently expandable explanations", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    for (const indicator of aiFixture.indicators) expect(screen.getByTestId(`ai-indicator-${indicator.id}`).tagName).toBe("DETAILS");
    expect(screen.getByRole("heading", { level: 1, name: "AI Industry" })).toBeTruthy();
    expect(screen.getByText("6 measures")).toBeTruthy();
    expect(screen.getByText("3 sources")).toBeTruthy();
  });
  it("filters coverage without dropping unknown publishers", () => {
    render(<AiInfrastructureView data={aiFixture} error={null} loading={false} refresh={() => {}} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a source" }), { target: { value: "Fixture publisher" } });
    const board = screen.getByTestId("ai-source-coverage");
    expect(board.querySelector('[data-testid="ai-coverage-fixture"]')).toBeTruthy();
    expect(board.querySelector('[data-testid="ai-coverage-ramp"]')).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "Find a source" }), { target: { value: "no matching publisher" } });
    expect(screen.getByText("No sources match your search.")).toBeTruthy();
  });
  it("separates capability from adoption and retains registry additions", () => {
    const capability = { ...aiFixture.indicators[0], id: "D6", title: "Model benchmark" };
    const future = { ...aiFixture.indicators[0], id: "D99", title: "New publisher metric" };
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [...aiFixture.indicators, capability, future] }} error={null} loading={false} refresh={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Measure" }).textContent).toContain("New publisher metric");
    expect(screen.getByRole("combobox", { name: "Measure" }).textContent).not.toContain("Design-task model quality");
    fireEvent.click(screen.getByRole("button", { name: "Model capability" }));
    expect(screen.getByRole("combobox", { name: "Measure" }).textContent).toContain("Design-task model quality");
    expect(screen.getByTestId("ai-indicator-D99").textContent).toContain(future.methodology);
  });
  it("keeps series identities and units distinct in the picker", () => {
    const first = aiFixture.indicators[0];
    const extra = first.history.map(point => ({ ...point, unit: "requests", series_id: "requests-v2", source_id: "other" }));
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [{ ...first, history: [...first.history, ...extra] }] }} error={null} loading={false} refresh={() => {}} />);
    const select = screen.getByRole("combobox", { name: "Observation series" });
    expect(select.querySelectorAll("option")).toHaveLength(2);
    expect(select.textContent).toContain("requests");
    expect(select.textContent).not.toContain("requests-v2");
    expect([...select.querySelectorAll("option")].map(option => option.value)).toEqual(["0", "1"]);
    expect(select.textContent).toContain("Fixture publisher");
  });
});
