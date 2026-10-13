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
import { readFileSync } from "fs";
import { resolve } from "path";

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
    // the rule row renders the HUMAN-READABLE, sentence-case predicate
    // "Flow strength > 70" (not the raw snake_case metric key)
    expect(screen.getByText(/Flow strength\s*>\s*70/)).toBeTruthy();
    expect(screen.getByText("1 rule")).toBeTruthy();
  });

  it("renders the empty state when there are no rules", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [] }), { status: 200 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());
  });

  it("speaks in sentence case: title, count, labels and empty copy (no-slop 11)", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [] }), { status: 200 })]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());
    expect(screen.getByRole("heading", { name: "Signal alerts" })).toBeTruthy();
    expect(screen.getByText("No rules")).toBeTruthy();
    expect(screen.getByText("Send to")).toBeTruthy();
    const empty = screen.getByText(/no alert rules/i);
    expect(empty.className).toContain("alerts-empty");
    expect(empty.textContent).toContain("Get pinged when flow crosses a line, e.g. AAPL flow strength > 70.");
    expect(screen.getByRole("option", { name: "Flow strength" })).toBeTruthy();
  });

  it("alerts CSS keeps labels, button and empty copy out of tracked uppercase mono (no-slop 11)", () => {
    const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");
    const block = (selector: string) => {
      const start = css.indexOf(`${selector} {`);
      expect(start).toBeGreaterThan(-1);
      return css.slice(start, css.indexOf("}", start));
    };
    for (const sel of [".alerts-form__label", ".alerts-form__select", ".alerts-form__submit", ".alerts-empty", ".alerts-count"]) {
      const body = block(sel);
      expect(body).toContain("text-transform: none");
      expect(body).toContain("font-family: var(--font-sans)");
    }
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

  it("recovers from a load error via the toast retry button", async () => {
    mockFetchSequence([
      () => new Response("boom", { status: 500 }),
      () => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 }),
    ]);
    const { container } = render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/failed/i));
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(screen.getByRole("alert").closest("#radon-toast-viewport")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
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

  it("sentence-cases select labels via an opt-out class while posting raw keys", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetchSequence([
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
      (url, init) => {
        calls.push({ url, init });
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      },
      () => new Response(JSON.stringify({ rules: [] }), { status: 200 }),
    ]);
    render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText(/no alert rules/i)).toBeTruthy());

    for (const name of [/metric/i, /operator/i, /channel/i]) {
      expect(screen.getByLabelText(name).classList.contains("alerts-form__select")).toBe(true);
    }
    expect(screen.getByRole("option", { name: "Flow strength" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Pushover" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/ticker/i), { target: { value: "AAPL" } });
    fireEvent.change(screen.getByLabelText(/threshold/i), { target: { value: "70" } });
    expect(calls).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /add rule/i }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("/api/alerts");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      ticker: "AAPL",
      metric: "flow_strength",
      op: ">",
      threshold: 70,
      channel: "pushover",
    });
  });

  it("renders no raw hex colors", async () => {
    mockFetchSequence([() => new Response(JSON.stringify({ rules: [SAMPLE_RULE] }), { status: 200 })]);
    const { container } = render(<AlertsPanel />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
