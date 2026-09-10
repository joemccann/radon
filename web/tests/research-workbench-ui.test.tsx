/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ResearchWorkbench from "../components/research/ResearchWorkbench";
import ResearchControls from "../components/research/ResearchControls";
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/useNewsfeedPosts", () => ({ useNewsfeedPosts: () => ({ posts: [], loading: false, error: null, refresh: vi.fn() }) }));
vi.mock("@/lib/agent/askBus", () => ({ emitAsk: vi.fn() }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function addSource(text = "2026-Q2 Revenue: $120 million\n2026-Q2 Capex: $30 million") {
  fireEvent.change(screen.getByLabelText("Document title"), { target: { value: "Quarterly filing" } });
  fireEvent.change(screen.getByLabelText("Ticker (optional)"), { target: { value: "AAPL" } });
  fireEvent.change(screen.getByLabelText("Published"), { target: { value: "2026-09-01" } });
  fireEvent.change(screen.getByLabelText("Original HTTPS URL (optional)"), { target: { value: "https://example.com/filing" } });
  fireEvent.change(screen.getByLabelText("Source text"), { target: { value: text } });
  fireEvent.submit(screen.getByRole("button", { name: "Add source", exact: true }).closest("form")!);
}
describe("source-grounded research workbench", () => {
  it("extracts cited figures, preserves exact passages, and computes a comparable capex ratio", () => {
    render(<ResearchWorkbench />); addSource();
    expect(screen.getByText(/1 sources/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Fundamentals", exact: true }));
    const table = screen.getByRole("table");
    expect(within(table).getByText("120")).toBeTruthy();
    expect(within(table).getByText("30")).toBeTruthy();
    expect(within(table).getAllByText("USD million")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "AI infrastructure", exact: true }));
    expect(screen.getByText("Capex / revenue: 25.0%")).toBeTruthy();
    expect(screen.getByText(/Not separately disclosed/)).toBeTruthy();
  });
  it("rejects unsafe sources and preserves the empty workspace", () => {
    render(<ResearchWorkbench />);
    fireEvent.change(screen.getByLabelText("Document title"), { target: { value: "Unsafe" } });
    fireEvent.change(screen.getByLabelText("Published"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Original HTTPS URL (optional)"), { target: { value: "javascript:alert(1)" } });
    fireEvent.change(screen.getByLabelText("Source text"), { target: { value: "An imported claim" } });
    fireEvent.submit(screen.getByRole("button", { name: "Add source", exact: true }).closest("form")!);
    expect(screen.getByRole("alert").textContent).toMatch(/HTTPS/);
    expect(screen.getByText(/0 sources/)).toBeTruthy();
  });
  it("rejects unsupported citations rather than attaching invented numeric evidence", () => {
    render(<ResearchWorkbench />); addSource("2026-Q2 Revenue: $120 million");
    fireEvent.click(screen.getByText("Annotate a cited fact"));
    fireEvent.change(screen.getByLabelText("Fact label"), { target: { value: "Invented revenue" } });
    fireEvent.change(screen.getByLabelText("Exact cited passage"), { target: { value: "2026-Q2 Revenue: $120 million" } });
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "2026-Q2" } });
    fireEvent.change(screen.getByLabelText("Signed value"), { target: { value: "999" } });
    fireEvent.change(screen.getByLabelText("Unit"), { target: { value: "USD million" } });
    fireEvent.submit(screen.getByRole("button", { name: "Add cited fact" }).closest("form")!);
    expect(screen.getByRole("alert").textContent).toMatch(/signed number/);
  });
  it("holds ticket handoff until every checklist observation is reviewed", () => {
    render(<ResearchWorkbench />); addSource();
    const apply = screen.getByRole("button", { name: "Apply checklist to ticket" }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    for (const checkbox of screen.getAllByRole("checkbox")) fireEvent.click(checkbox);
    expect(apply.disabled).toBe(false);
  });
});
describe("governance availability", () => {
  it("fails closed on malformed audit payloads", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ schemaVersion: 1, policy: [] }) }));
    render(<ResearchControls />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/unsupported format/);
    expect(screen.queryByRole("link", { name: "Download audit JSON" })).toBeNull();
  });
  it("reports access denial without inventing audit rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403 }));
    render(<ResearchControls />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/operator access/);
    expect(screen.queryByText(/records/)).toBeNull();
  });
});
