/**
 * @vitest-environment jsdom
 *
 * NF-1 follow-up: the "Enforce 2.5% bankroll cap on all placers" toggle is
 * tested at the wire. Renders PreferencesSection (the component that owns the
 * fetch through lib/preferences) with global fetch stubbed, and asserts the
 * exact PUT that turns enforcement On, plus that nothing is written before Save.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import PreferencesSection from "@/components/PreferencesSection";

const KEY = "RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS";

const ENTRY = {
  key: KEY,
  label: "Enforce 2.5% bankroll cap on all placers",
  group: "Order Limits",
  value_type: "bool",
  value: false,
  default: false,
  hard_min: false,
  hard_max: true,
  unit: "",
  description: "On: ib_execute and the exit order service enforce Gate 3.",
  applies_immediately: true,
  risk_gated: true,
  source: "default",
  db_rejected: false,
  updated_at: null,
  updated_by: null,
};

const STORE = { available: true, error: null, checked_at: "2026-09-19T00:00:00Z" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "PUT") {
      return json({ preference: { ...ENTRY, value: true, source: "db" }, store: STORE });
    }
    return json({
      preferences: [ENTRY],
      groups: ["Order Limits"],
      store: STORE,
      generated_at: "2026-09-19T00:00:00Z",
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const puts = () => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT");

describe("bankroll cap enforce toggle", () => {
  it("sends PUT /api/preferences {key, value: true} only after Save", async () => {
    render(<PreferencesSection />);
    const box = (await screen.findByTestId(`preference-input-${KEY}`)) as HTMLInputElement;
    expect(box.checked).toBe(false);

    fireEvent.click(box);
    expect(puts()).toHaveLength(0);

    fireEvent.click(screen.getByTestId(`preference-save-${KEY}`));
    await waitFor(() => expect(puts()).toHaveLength(1));

    const [url, init] = puts()[0] as [string, RequestInit];
    expect(url).toBe("/api/preferences");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ key: KEY, value: true });
  });
});
