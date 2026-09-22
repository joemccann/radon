/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
    context: { pageCount: 14, figureCount: 6, dateSource: "text", excerpt: "# Market Watch\n\nFlows & Liquidity. How much upward pressure on global bond yields from tech bond issuance?",
      selectorReason: "One measured finding on tech issuance. The labor market remains the swing factor through year-end.", sourceUrl: "/api/newsfeed/research/files/" + "c".repeat(64) + ".pdf" } },
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

  it("shows report meta, the full selector note, and the PDF, not the raw article excerpt", async () => {
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).getByText("Report dated 2026-09-16 (from the document text) · 14 pages · 6 charts")).toBeTruthy();
    expect(within(card).queryByRole("heading", { name: "Market Watch" })).toBeNull();
    expect(card.textContent).not.toContain("Flows & Liquidity. How much upward pressure on global bond yields from tech bond issuance?");
    const note = within(card).getByTestId("held-selector-note");
    expect(note.textContent).toContain("Selector's note:");
    expect(note.textContent).toContain("One measured finding on tech issuance. The labor market remains the swing factor through year-end.");
    const link = within(card).getByRole("link", { name: "Open PDF" });
    expect(link.getAttribute("href")).toBe("/api/newsfeed/research/files/" + "c".repeat(64) + ".pdf");
    expect(link.getAttribute("target")).toBe("_blank");
    const bare = screen.getByText("gbpusd_en_1666701.pdf").closest("li") as HTMLElement;
    expect(within(bare).queryByRole("link", { name: "Open PDF" })).toBeNull();
  });

  it("keeps a long selector note fully in the DOM and scrollable", async () => {
    const longNote = "Two incremental measured findings selected from this Standard Chartered note. "
      + "The UBS Fed item already covered the dot plot rounding and target range. "
      + "The labor market remains the unresolved swing factor through year-end. "
      + "End of selector note.";
    const long = [...items];
    long[0] = { ...items[0], context: { ...items[0].context, excerpt: "# Market Watch\n\nThe US Federal Reserve hiked.", selectorReason: longNote } };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: long, pending: 34 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).queryByRole("heading", { name: "Market Watch" })).toBeNull();
    expect(card.textContent).not.toContain("The US Federal Reserve hiked.");
    const note = within(card).getByTestId("held-selector-note");
    expect(note.textContent).toContain(longNote);
    expect(note.textContent).toContain("End of selector note.");
    const css = readFileSync(resolve(__dirname, "../components/ResearchHeldReview.module.css"), "utf8");
    const start = css.indexOf(".reason {");
    expect(start, ".reason rule missing").toBeGreaterThan(-1);
    const block = css.slice(start, css.indexOf("}", start));
    expect(block).toMatch(/overflow-y:\s*auto/);
    expect(block).toMatch(/max-height:/);
    expect(block).toMatch(/min-height:\s*0/);
  });

  it("renders the draft body as markdown, not raw source, and never mounts the article excerpt", async () => {
    const markdown = [...items];
    markdown[0] = { ...items[0], context: { ...items[0].context, excerpt: "### Recommendation Change\n\n|Ticker|Price|\n|---|---|\n|AZN|11,708.00|" },
      drafts: [{ ...items[0].drafts[0], content: "**Tozo** doubled" }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: markdown, pending: 34 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).queryByRole("heading", { name: "Recommendation Change" })).toBeNull();
    expect(within(card).queryByRole("cell", { name: "11,708.00" })).toBeNull();
    fireEvent.click(within(card).getByText("Tech issuance adds 10-20bp"));
    expect(within(card).getByText("Tozo").tagName).toBe("STRONG");
  });

  it("does not render excerpt markdown links; draft links stay inert text", async () => {
    const hostile = [...items];
    hostile[0] = { ...items[0], context: { ...items[0].context, excerpt: "Urgent: [verify your account](https://evil.example/phish)" },
      drafts: [{ ...items[0].drafts[0], content: "[chart source](https://example.com/ok)" }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: hostile, pending: 34 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_flows___liquidity.pdf")).closest("li") as HTMLElement;
    expect(within(card).queryByRole("link", { name: "verify your account" })).toBeNull();
    expect(within(card).queryByText("verify your account")).toBeNull();
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

  it("labels TEXT_ONLY_WITH_FIGURES on a Held card", async () => {
    const held = [{
      workKey: "d".repeat(64), fileName: "jpm_credit.pdf", publisher: "J.P. Morgan", series: "daily credit",
      docType: "research", folderDate: "2026-09-10", documentDate: "2026-09-10", outcome: "held",
      reasonCodes: ["TEXT_ONLY_WITH_FIGURES"],
      drafts: [{ title: "Hyperscaler HG fundamentals", content: "Draft", held: "TEXT_ONLY_WITH_FIGURES" }],
      context: { pageCount: 8, figureCount: 4, dateSource: "text", excerpt: "Daily Credit Strategy Update", selectorReason: "measured finding" },
    }];
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ items: held, pending: 1 }), { status: 200 }));
    render(<ResearchHeldReview />);
    const card = (await screen.findByText("jpm_credit.pdf")).closest("li") as HTMLElement;
    expect(within(card).getAllByText("Text-only draft while the cited pages have charts").length).toBeGreaterThan(0);
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
