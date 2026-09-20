/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResearchHeldReview from "../components/ResearchHeldReview";

// The rules panel has its own wire tests (research-rules-ui.test.tsx); here it would only add a second GET.
vi.mock("../components/ResearchRuleProposals", () => ({ default: () => null }));

const KEY_A = "a".repeat(64), KEY_B = "b".repeat(64);
const items = [
  { workKey: KEY_A, fileName: "jpm_flows___liquidity.pdf", publisher: "J.P. Morgan", series: "jpm flows liquidity", docType: "research", folderDate: "2026-09-17",
    documentDate: "2026-09-16", outcome: "held", reasonCodes: ["NUMBER_NOT_ON_PAGE"],
    drafts: [{ title: "Tech issuance adds 10-20bp", content: "Draft body", held: "NUMBER_NOT_ON_PAGE", detail: "10-20bp" }],
    context: { pageCount: 14, figureCount: 6, dateSource: "text", excerpt: "Flows & Liquidity. How much upward pressure on global bond yields from tech bond issuance?",
      selectorReason: "One measured finding on tech issuance.", sourceUrl: "/api/newsfeed/research/files/" + "c".repeat(64) + ".pdf" } },
  { workKey: KEY_B, fileName: "gbpusd_en_1666701.pdf", publisher: "UBS", series: "gbpusd", docType: "fx_pair_note", folderDate: "2026-09-17",
    documentDate: "2026-09-17", outcome: "dropped", reasonCodes: ["DOC_TYPE_FX_PAIR_NOTE"], drafts: [] },
];
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (!init?.method || init.method === "GET") return new Response(JSON.stringify({ items, pending: 34 }), { status: 200 });
    return new Response(JSON.stringify({ id: "v1", vote: "up" }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("ResearchHeldReview", () => {
  it("loads today's sample from the held endpoint and shows why each document was held", async () => {
    render(<ResearchHeldReview />);
    await screen.findByText("jpm_flows___liquidity.pdf");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/newsfeed/research/held");
    expect((fetchMock.mock.calls[0][1] as RequestInit).cache).toBe("no-store");
    expect(screen.getByText("2 of 34 awaiting review")).toBeTruthy();
    const card = screen.getByText("jpm_flows___liquidity.pdf").closest("li") as HTMLElement;
    expect(within(card).getByText("Number not on the cited page")).toBeTruthy();
    expect(within(card).getByText("Tech issuance adds 10-20bp")).toBeTruthy();
    expect(within(card).getByText("10-20bp")).toBeTruthy();
    expect(screen.getByText("Document type: FX pair note")).toBeTruthy();
  });

  it("shows what the document is: report date, size, opening text, the selector's reason and the PDF", async () => {
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).getByText("Report dated 2026-09-16 (from the document text) · 14 pages · 6 charts")).toBeTruthy();
    expect(within(card).getByText("Flows & Liquidity. How much upward pressure on global bond yields from tech bond issuance?")).toBeTruthy();
    expect(within(card).getByText("One measured finding on tech issuance.")).toBeTruthy();
    expect(within(card).getByText("Selector's note:")).toBeTruthy();
    const link = within(card).getByRole("link", { name: "Open PDF" });
    expect(link.getAttribute("href")).toBe("/api/newsfeed/research/files/" + "c".repeat(64) + ".pdf");
    expect(link.getAttribute("target")).toBe("_blank");
    const bare = screen.getByText("gbpusd_en_1666701.pdf").closest("li") as HTMLElement;
    expect(within(bare).queryByRole("link", { name: "Open PDF" })).toBeNull();
  });

  it("renders the excerpt and the draft body as markdown, not raw source", async () => {
    const markdown = [...items];
    markdown[0] = { ...items[0], context: { ...items[0].context, excerpt: "### Recommendation Change\n\n|Ticker|Price|\n|---|---|\n|AZN|11,708.00|" },
      drafts: [{ ...items[0].drafts[0], content: "**Tozo** doubled" }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: markdown, pending: 34 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).getByRole("heading", { name: "Recommendation Change" })).toBeTruthy();
    expect(within(card).getByRole("cell", { name: "11,708.00" })).toBeTruthy();
    expect(card.textContent).not.toContain("###");
    expect(card.textContent).not.toContain("|---|");
    fireEvent.click(within(card).getByText("Tech issuance adds 10-20bp"));
    expect(within(card).getByText("Tozo").tagName).toBe("STRONG");
  });

  it("excerpt and draft links are inert text", async () => {
    const hostile = [...items];
    hostile[0] = { ...items[0], context: { ...items[0].context, excerpt: "Urgent: [verify your account](https://evil.example/phish)" },
      drafts: [{ ...items[0].drafts[0], content: "[chart source](https://example.com/ok)" }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: hostile, pending: 34 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    // Untrusted PDF text must not render a clickable attacker-chosen href.
    expect(within(card).queryByRole("link", { name: "verify your account" })).toBeNull();
    expect(within(card).getByText("verify your account").tagName).toBe("SPAN");
    // Held drafts are the candidates that FAILED verification: same
    // untrusted-PDF provenance, so their links are inert too.
    fireEvent.click(within(card).getByText("Tech issuance adds 10-20bp"));
    expect(within(card).queryByRole("link", { name: "chart source" })).toBeNull();
    expect(within(card).getByText("chart source").tagName).toBe("SPAN");
  });

  it("a should-have-published vote posts the work key and removes the card", async () => {
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Should have published" }));
    fireEvent.change(within(card).getByLabelText("Comment (optional)"), { target: { value: "the range is in the table on page 3" } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(within(card).getByRole("button", { name: "Save feedback" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/newsfeed/research/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ workKey: KEY_A, vote: "up", reasons: [], comment: "the range is in the table on page 3" });
    await waitFor(() => expect(screen.queryByText("jpm_flows___liquidity.pdf")).toBeNull());
    expect(screen.getByText("gbpusd_en_1666701.pdf")).toBeTruthy();
  });

  it("confirming a hold posts a down vote", async () => {
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("gbpusd_en_1666701.pdf")).closest("li") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "Correct to hold" }));
    fireEvent.click(within(card).getByRole("button", { name: "Save feedback" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({ workKey: KEY_B, vote: "down", reasons: [], comment: "" });
  });

  it("shows an empty state and an error toast", async () => {
    fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify({ items: [], pending: 0 }), { status: 200 }));
    render(<ResearchHeldReview />);
    await screen.findByText("Nothing held is waiting for review.");
    cleanup();
    fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify({ error: "Held review temporarily unavailable." }), { status: 503 }));
    render(<ResearchHeldReview />);
    expect((await screen.findByRole("alert")).textContent).toContain("Held review temporarily unavailable.");
  });
});
