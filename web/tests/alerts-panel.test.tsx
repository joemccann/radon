/**
 * @vitest-environment jsdom
 *
 * F6 — signal alert rules engine UI.
 *
 * Behaviours pinned:
 *  - AlertsPanel lists existing rules from /api/alerts
 *  - creating a rule POSTs the form payload and re-renders the new row
 *  - deleting a rule DELETEs by id and drops the row
 *  - an empty rule set renders the empty state, not an error
 *  - a fetch failure surfaces the error, not a silent blank
 *  - brand tokens only — no raw hex in the rendered markup
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";

import { AlertsPanel } from "@/components/alerts/AlertsPanel";

const SAMPLE_RULE = {
  id: "rule-1",
  ticker: "AAPL",
  metric: "flow_strength",
  op: ">",
  threshold: 70,
  channel: "pushover",
  created_at: "2026-06-21T12:00:00Z",
  last_fired_at: null,
};

function mockFetchSequence(handlers: Array<(url: string, init?: RequestInit) => Response>) {
  let call = 0;
  global.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const handler = handlers[Math.min(call, handlers.length - 1)];
    call += 1;
    return handler(String(url), init);
  }) as typeof fetch;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AlertsPanel", () => {
  it("lists existing rules", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    // the rule row renders the HUMAN-READABLE predicate "Flow Strength > 70"
    // (not the raw snake_case metric key)
    expect(screen.getByText(/Flow Strength\s*>\s*70/)).toBeTruthy();
  });

  it("renders the empty state when there are no rules", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [] }), { status: 200 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());
  });

  it("surfaces an error when the list fetch fails", async () => {
    mockFetchSequence([() => new Response("boom", { status: 500 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeTruthy());
  });

  it("creates a rule and renders the new row", async () => {
    mockFetchSequence([
      // initial GET — empty
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
      // POST create
      (_url, init) => {
        expect(init?.method).toBe("POST");
        const body = JSON.parse(String(init?.body));
        expect(body.ticker).toBe("AAPL");
        expect(body.metric).toBe("flow_strength");
        expect(body.op).toBe(">");
        expect(body.threshold).toBe(70);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      },
      // refetch GET — now has the rule
      () => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 }),
    ]);

    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/ticker/i), { target: { value: "aapl" } });
    fireEvent.change(screen.getByLabelText(/metric/i), { target: { value: "flow_strength" } });
    fireEvent.change(screen.getByLabelText(/operator/i), { target: { value: ">" } });
    fireEvent.change(screen.getByLabelText(/threshold/i), { target: { value: "70" } });
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));

    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
  });

  it("deletes a rule by id", async () => {
    mockFetchSequence([
      // initial GET
      () => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 }),
      // DELETE
      (url, init) => {
        expect(init?.method).toBe("DELETE");
        expect(url).toContain("rule-1");
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      },
      // refetch GET — empty
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
    ]);

    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());

    // delete button now carries a rule-specific label
    fireEvent.click(screen.getByRole("button", { name: /remove alert aapl/i }));
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());
  });

  it("recovers from a load error via the Retry button", async () => {
    mockFetchSequence([
      () => new Response("boom", { status: 500 }),
      () => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 }),
    ]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
  });

  it("surfaces last_fired_at as a relative time, or 'Never fired'", async () => {
    const fired = { ...SAMPLE_RULE, id: "rule-2", last_fired_at: new Date(Date.now() - 3600_000).toISOString() };
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [SAMPLE_RULE, fired] }), { status: 200 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getAllByText("AAPL").length).toBe(2));
    expect(screen.getByText(/never fired/i)).toBeTruthy();
    expect(screen.getByText(/^fired /i)).toBeTruthy();
  });

  it("rejects a threshold outside the metric's range before POSTing", async () => {
    const post = vi.fn();
    mockFetchSequence([
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
      (_url, init) => {
        post(init);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      },
    ]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/ticker/i), { target: { value: "AAPL" } });
    fireEvent.change(screen.getByLabelText(/^metric$/i), { target: { value: "buy_ratio" } });
    fireEvent.change(screen.getByLabelText(/threshold/i), { target: { value: "70" } });
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));

    // buy_ratio range is 0-1, so 70 is rejected client-side with no POST
    await waitFor(() => expect(screen.getByText(/between 0 and 1/i)).toBeTruthy());
    expect(post).not.toHaveBeenCalled();
  });

  it("offers a channel selector and sends the chosen channel", async () => {
    mockFetchSequence([
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
      (_url, init) => {
        const body = JSON.parse(String(init?.body));
        expect(body.channel).toBe("service_health");
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      },
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
    ]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/ticker/i), { target: { value: "AAPL" } });
    fireEvent.change(screen.getByLabelText(/threshold/i), { target: { value: "70" } });
    fireEvent.change(screen.getByLabelText(/channel/i), { target: { value: "service_health" } });
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());
  });

  it("renders no raw hex colors", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 })]);
    const { container } = render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
