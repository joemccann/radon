// @vitest-environment jsdom
//
// REL-144 / R-408: a fallback to IB's realized P&L is VISIBLE.
//
// `apply_journal_realized_pnl` faithfully stamps
// `realizedPNLSource: "journal" | "ib"` and preserves `ibRealizedPNL`, and the
// field is declared in `types.ts` and asserted in five test files -- but
// grepping `web/components` and `web/app` for it returned ZERO hits. Every
// withholding path in `journal_realized.py` therefore degraded to IB's drifted
// number with no marker, the only trace a WARNING in a systemd journal nobody
// watches during a session. For the SLV shape the module documents that is an
// $11,558 discrepancy rendered as if it were the journal-derived truth.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import FillsModal from "../components/FillsModal";

function fill(overrides: Record<string, unknown> = {}) {
  return {
    execId: "e1",
    time: "2026-08-28T18:30:00Z",
    symbol: "SLV C60",
    side: "SLD",
    quantity: 250,
    avgPrice: 3.2,
    commission: -1.25,
    realizedPNL: 18511,
    ...overrides,
  };
}

function renderModal(fills: unknown[]) {
  return render(
    React.createElement(FillsModal, {
      open: true,
      fills,
      totalRealizedPnl: (fills as Array<{ realizedPNL?: number }>).reduce(
        (sum, f) => sum + (f.realizedPNL ?? 0),
        0,
      ),
      onClose: vi.fn(),
      netLiquidation: 100_000,
    } as never),
  );
}

afterEach(cleanup);

describe("realized P&L provenance", () => {
  it("marks a row that fell back to IB's figure", () => {
    renderModal([fill({ realizedPNLSource: "ib", ibRealizedPNL: 18511 })]);
    const marker = screen.getByTestId("realized-source-e1");
    expect(marker.textContent).toContain("IB");
    expect(marker.getAttribute("title")).toMatch(/journal/i);
  });

  it("does not mark a journal-derived figure", () => {
    renderModal([fill({ realizedPNLSource: "journal" })]);
    expect(screen.queryByTestId("realized-source-e1")).toBeNull();
  });

  it("does not mark a fill the module never stamped", () => {
    renderModal([fill()]);
    expect(screen.queryByTestId("realized-source-e1")).toBeNull();
  });

  it("does not mark an ib-sourced fill with no IB figure to show", () => {
    renderModal([fill({ realizedPNLSource: "ib", ibRealizedPNL: null })]);
    expect(screen.queryByTestId("realized-source-e1")).toBeNull();
  });

  it("marks only the fills that fell back", () => {
    renderModal([
      fill({ execId: "a", realizedPNLSource: "journal" }),
      fill({ execId: "b", realizedPNLSource: "ib", ibRealizedPNL: 4200 }),
    ]);
    expect(screen.queryByTestId("realized-source-a")).toBeNull();
    expect(screen.getByTestId("realized-source-b")).toBeTruthy();
  });
});
