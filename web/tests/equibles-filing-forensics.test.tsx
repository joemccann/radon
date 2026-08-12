/**
 * @vitest-environment jsdom
 *
 * Pre-trade filing forensics — the dossier surface.
 *
 * The behaviour that matters is the four-way verdict split: FLAGGED, CLEAR,
 * NO FILINGS FOUND, COULD NOT VERIFY. The last two must never render as an
 * all clear. The read route is covered in equibles-filing-forensics-route.test.ts.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

type Row = Record<string, unknown>;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function check(overrides: Row = {}): Row {
  return {
    code: "dilution_overhang",
    label: "Dilution overhang",
    gate: 3,
    tone: "adverse",
    verdict: "flagged",
    detail: "$45.0M of unused at-the-market shelf capacity across 1 live program(s).",
    figure: { value: 45_000_000, unit: "usd", label: "UNUSED ATM CAPACITY" },
    filing: { form: "424B5", filed_at: "2026-07-02", url: "https://sec.gov/atm.htm" },
    ...overrides,
  };
}

function dossier(overrides: Row = {}): Row {
  return {
    ticker: "AAPL",
    as_of: new Date().toISOString(),
    data_complete: true,
    flag_count: 1,
    flags: [check()],
    checks: [check()],
    sources: { atm_programs: { status: "ok", count: 1, error: null } },
    ...overrides,
  };
}

function stubFetch(payload: Row, status = 200): void {
  global.fetch = vi.fn(
    async () => new Response(JSON.stringify(payload), { status }),
  ) as typeof fetch;
}

async function renderDossier() {
  const { default: FilingForensicsDossier } = await import(
    "@/components/equibles/FilingForensicsDossier"
  );
  return render(<FilingForensicsDossier ticker="AAPL" />);
}

describe("FilingForensicsDossier", () => {
  it("renders a flagged check with its figure and the filing it cites", async () => {
    stubFetch(dossier());
    await renderDossier();

    await waitFor(() => expect(screen.getByText("Dilution overhang")).toBeTruthy());
    expect(screen.getByTestId("filing-verdict-dilution_overhang").textContent).toBe("FLAGGED");
    expect(screen.getByTestId("filing-figure-dilution_overhang").textContent).toBe("$45.0M");
    const citation = screen.getByTestId("filing-citation") as HTMLAnchorElement;
    expect(citation.getAttribute("href")).toBe("https://sec.gov/atm.htm");
    expect(citation.textContent).toContain("424B5");
  });

  it("renders an empty source as NO FILINGS FOUND, not as clear", async () => {
    stubFetch(
      dossier({
        flag_count: 0,
        flags: [],
        checks: [
          check({
            verdict: "no-filings",
            detail: "No filings found",
            figure: null,
            filing: null,
          }),
        ],
      }),
    );
    await renderDossier();

    await waitFor(() =>
      expect(screen.getByTestId("filing-verdict-dilution_overhang").textContent).toBe(
        "NO FILINGS FOUND",
      ),
    );
    expect(screen.queryByText("CLEAR")).toBeNull();
  });

  it("renders a failed source as COULD NOT VERIFY plus a partial-data note", async () => {
    stubFetch(
      dossier({
        data_complete: false,
        flag_count: 0,
        flags: [],
        checks: [
          check({
            verdict: "unavailable",
            detail: "Could not verify: the filing source did not answer",
            figure: null,
            filing: null,
          }),
        ],
      }),
    );
    await renderDossier();

    await waitFor(() =>
      expect(screen.getByTestId("filing-verdict-dilution_overhang").textContent).toBe(
        "COULD NOT VERIFY",
      ),
    );
    expect(screen.getByTestId("filing-forensics-partial").textContent).toMatch(/not clear/i);
    expect(screen.queryByText("CLEAR")).toBeNull();
  });

  it("distinguishes a genuine clear from both absence states", async () => {
    stubFetch(
      dossier({
        flag_count: 0,
        flags: [],
        checks: [check({ verdict: "clear", detail: "No material shelf capacity", figure: null, filing: null })],
      }),
    );
    await renderDossier();

    await waitFor(() =>
      expect(screen.getByTestId("filing-verdict-dilution_overhang").textContent).toBe("CLEAR"),
    );
    expect(screen.queryByText("NO FILINGS FOUND")).toBeNull();
    expect(screen.queryByText("COULD NOT VERIFY")).toBeNull();
  });

  it("renders an absent dossier as 'no dossier yet', not as an all clear", async () => {
    stubFetch({ missing: true, ticker: "ZZZZ", checks: [], data_complete: false });
    await renderDossier();

    await waitFor(() => expect(screen.getByText(/no dossier yet/i)).toBeTruthy());
    expect(screen.getByText(/not the same as no overhang/i)).toBeTruthy();
  });

  it("surfaces a fetch failure as unknown, never as an all clear", async () => {
    stubFetch({}, 500);
    await renderDossier();

    await waitFor(() => expect(screen.getByText(/filing forensics unavailable/i)).toBeTruthy());
    expect(screen.getByText(/not as an all clear/i)).toBeTruthy();
  });

  it("labels each check with the gate it feeds", async () => {
    stubFetch(
      dossier({
        checks: [check(), check({ code: "insider_supply_pending", label: "Insider supply pending", gate: 2 })],
      }),
    );
    await renderDossier();

    await waitFor(() => expect(screen.getByText("GATE 3 RISK")).toBeTruthy());
    expect(screen.getByText("GATE 2 EDGE")).toBeTruthy();
  });

  it("derives its freshness label from the data timestamp, not hardcoded copy", async () => {
    stubFetch(dossier({ as_of: "2026-07-04T13:00:00Z" }));
    await renderDossier();

    await waitFor(() =>
      expect(screen.getByTestId("filing-forensics-as-of").textContent).toMatch(/JUL 4/),
    );
  });

  it("renders no raw hex colors", async () => {
    stubFetch(dossier());
    const { container } = await renderDossier();
    await waitFor(() => expect(screen.getByText("Dilution overhang")).toBeTruthy());
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

// ── pure helpers ─────────────────────────────────────────────────

describe("filing forensics formatting", () => {
  it("keeps a supportive flag visually distinct from an adverse one", async () => {
    const { verdictColor } = await import("@/components/equibles/FilingForensicsDossier");
    expect(verdictColor({ verdict: "flagged", tone: "adverse" })).toBe("var(--negative)");
    expect(verdictColor({ verdict: "flagged", tone: "supportive" })).toBe("var(--positive)");
    expect(verdictColor({ verdict: "unavailable", tone: "adverse" })).toBe("var(--warning)");
  });

  it("formats dollar and count figures", async () => {
    const { formatFigure } = await import("@/components/equibles/FilingForensicsDossier");
    expect(formatFigure({ value: 2_400_000_000, unit: "usd", label: "x" })).toBe("$2.4B");
    expect(formatFigure({ value: 45_000_000, unit: "usd", label: "x" })).toBe("$45.0M");
    expect(formatFigure({ value: 3, unit: "count", label: "x" })).toBe("3");
    expect(formatFigure(null)).toBeNull();
  });
});
