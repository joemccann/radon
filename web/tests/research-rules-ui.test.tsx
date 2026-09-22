/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResearchRuleProposals from "../components/ResearchRuleProposals";

const proposed = [{ id: "series_deny:ubs cio fx view", kind: "series_deny", key: "ubs cio fx view", downs: 4, ups: 0, evidence: 4 }];
const approved = [{ id: "doc_type_drop:single_stock", kind: "doc_type_drop", key: "single_stock", downs: 7, ups: 0, evidence: 7 }];
const rejected = [{ id: "publisher_deny:Maxim Group", kind: "publisher_deny" as const, key: "Maxim Group", downs: 6, ups: 0, evidence: 6 }];
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => init?.method === "POST"
    ? new Response(JSON.stringify({ id: "x", status: "approved" }), { status: 200 })
    : new Response(JSON.stringify({ proposed, approved, rejected: [] }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function posted(index: number) {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return { url, method: init.method, body: JSON.parse(String(init.body)) };
}

describe("ResearchRuleProposals", () => {
  it("explains each proposal in plain words and sends nothing until a decision is clicked", async () => {
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing the series “ubs cio fx view”")).closest("li") as HTMLElement;
    expect(within(item).getByText("You rejected 4 of its items and approved none.")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/newsfeed/research/rules");
  });

  it("Approve posts the exact decision and moves the rule to the active list", async () => {
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing the series “ubs cio fx view”")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: "Approve rule" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(posted(1)).toEqual({ url: "/api/newsfeed/research/rules", method: "POST", body: { id: "series_deny:ubs cio fx view", decision: "approve" } });
    const active = await screen.findByRole("list", { name: "Active rules" });
    await waitFor(() => expect(within(active).getByText("Stop reviewing the series “ubs cio fx view”")).toBeTruthy());
  });

  it("Reject and Revoke move the rule into Rejected rules", async () => {
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing the series “ubs cio fx view”")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(posted(1).body).toEqual({ id: "series_deny:ubs cio fx view", decision: "reject" }));
    const rejectedList = await screen.findByRole("list", { name: "Rejected rules" });
    await waitFor(() => expect(within(rejectedList).getByText("Stop reviewing the series “ubs cio fx view”")).toBeTruthy());
    expect(screen.queryByRole("list", { name: "Proposed rules" })).toBeNull();
    const active = screen.getByRole("list", { name: "Active rules" });
    fireEvent.click(within(active).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(posted(2).body).toEqual({ id: "doc_type_drop:single_stock", decision: "revoke" }));
    await waitFor(() => expect(within(rejectedList).getByText("Drop every “single_stock” document before review")).toBeTruthy());
    expect(screen.queryByRole("list", { name: "Active rules" })).toBeNull();
  });

  it("Approve from Rejected posts the exact decision and moves the rule to Active rules", async () => {
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => init?.method === "POST"
      ? new Response(JSON.stringify({ id: rejected[0].id, status: "approved" }), { status: 200 })
      : new Response(JSON.stringify({ proposed: [], approved: [], rejected }), { status: 200 }));
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing everything from “Maxim Group”")).closest("li") as HTMLElement;
    expect(within(item).getByText("Rejected. Approve to make it active.")).toBeTruthy();
    fireEvent.click(within(item).getByRole("button", { name: "Approve rule" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(posted(1)).toEqual({ url: "/api/newsfeed/research/rules", method: "POST", body: { id: "publisher_deny:Maxim Group", decision: "approve" } });
    const active = await screen.findByRole("list", { name: "Active rules" });
    await waitFor(() => expect(within(active).getByText("Stop reviewing everything from “Maxim Group”")).toBeTruthy());
    expect(screen.queryByRole("list", { name: "Rejected rules" })).toBeNull();
  });

  it("renders nothing when proposed, approved and rejected are empty", async () => {
    fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify({ proposed: [], approved: [], rejected: [] }), { status: 200 }));
    const { container } = render(<ResearchRuleProposals />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("a failed decision raises an error toast and leaves the proposal in place", async () => {
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing the series “ubs cio fx view”")).closest("li") as HTMLElement;
    fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify({ error: "Rule proposals temporarily unavailable." }), { status: 503 }));
    fireEvent.click(within(item).getByRole("button", { name: "Approve rule" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Rule proposals temporarily unavailable.");
    expect(screen.getByText("Stop reviewing the series “ubs cio fx view”")).toBeTruthy();
  });

  it("a failed Approve from Rejected raises an error toast and leaves the card in Rejected rules", async () => {
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => init?.method === "POST"
      ? new Response(JSON.stringify({ error: "Rule proposals temporarily unavailable." }), { status: 503 })
      : new Response(JSON.stringify({ proposed: [], approved: [], rejected }), { status: 200 }));
    render(<ResearchRuleProposals />);
    const item = (await screen.findByText("Stop reviewing everything from “Maxim Group”")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: "Approve rule" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Rule proposals temporarily unavailable.");
    expect(within(screen.getByRole("list", { name: "Rejected rules" })).getByText("Stop reviewing everything from “Maxim Group”")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Active rules" })).toBeNull();
  });
});
