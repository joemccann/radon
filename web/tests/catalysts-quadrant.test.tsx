/**
 * @vitest-environment jsdom
 *
 * CatalystsQuadrant — category-grouped upcoming catalysts. Pinned behaviours:
 *  - ECONOMIC DATA / EARNINGS / FDA / remainder ordering
 *  - progressive disclosure: only the first category opens, headers toggle,
 *    long categories preview a few rows behind a SHOW ALL control
 *  - held tickers are flagged and lead their category
 *  - rail derives UPDATED time + event count from the payload
 *  - fossil snapshots (past-only rows) render the empty state
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react";

import CatalystsQuadrant from "@/components/dashboard/CatalystsQuadrant";
import type { CatalystData, CatalystRow } from "@/lib/useCatalysts";

let payload: CatalystData | null = null;

vi.mock("@/lib/useCatalysts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/useCatalysts")>();
  return {
    ...mod,
    useCatalysts: () => ({ data: payload, isLoading: false, error: null, refresh: vi.fn() }),
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  payload = null;
});

function freeze(instant: string) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(instant));
}

function row(overrides: Partial<CatalystRow>): CatalystRow {
  return {
    ticker: null,
    type: "economic",
    title: "Existing home sales",
    date: "2026-08-04",
    source: "economic",
    days_until: 0,
    ...overrides,
  };
}

function snapshot(catalysts: CatalystRow[]): CatalystData {
  return { scan_time: "2026-08-04T10:30:00Z", count: catalysts.length, catalysts };
}

function sectionFor(label: string): HTMLElement {
  const head = screen.getByRole("button", { name: new RegExp(label) });
  return head.closest(".catalyst-group") as HTMLElement;
}

describe("CatalystsQuadrant", () => {
  it("orders categories economic, earnings, fda", () => {
    freeze("2026-08-04T14:00:00Z"); // Tuesday 10:00 ET
    payload = snapshot([
      row({ type: "fda", ticker: "ABOS", title: "PDUFA date", date: "2026-08-06" }),
      row({ type: "earnings", ticker: "MCHP", title: "MCHP earnings", date: "2026-08-04" }),
      row({ title: "Core CPI", date: "2026-08-07" }),
    ]);
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    const labels = Array.from(document.querySelectorAll(".catalyst-group__label")).map(
      (el) => el.textContent,
    );
    expect(labels).toEqual(["ECONOMIC DATA", "EARNINGS", "FDA"]);
  });

  it("opens only the first category and toggles the others on click", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = snapshot([
      row({ title: "Core CPI", date: "2026-08-07" }),
      row({ type: "earnings", ticker: "MCHP", title: "MCHP earnings", date: "2026-08-04" }),
    ]);
    render(<CatalystsQuadrant positionTickers={new Set()} />);

    expect(screen.getByText("Core CPI")).toBeTruthy();
    expect(screen.queryByText(/MCHP earnings/)).toBeNull();

    const earnings = screen.getByRole("button", { name: /EARNINGS/ });
    expect(earnings.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(earnings);
    expect(earnings.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/MCHP · MCHP earnings/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /ECONOMIC DATA/ }));
    expect(screen.queryByText("Core CPI")).toBeNull();
  });

  it("previews a long category behind a SHOW ALL control", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = snapshot(
      Array.from({ length: 12 }, (_, i) =>
        row({ title: `Print ${i}`, date: "2026-08-05", days_until: 1 }),
      ),
    );
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    const section = sectionFor("ECONOMIC DATA");
    expect(section.querySelectorAll(".catalyst-group__row")).toHaveLength(6);

    expect(section.querySelector(".catalyst-group__rows--scroll")).toBeNull();

    const more = within(section).getByRole("button", { name: /SHOW ALL 12/ });
    fireEvent.click(more);
    expect(section.querySelectorAll(".catalyst-group__row")).toHaveLength(12);
    // A fully expanded category scrolls inside the panel instead of stretching it.
    expect(section.querySelector(".catalyst-group__rows--scroll")).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: /SHOW LESS/ }));
    expect(section.querySelectorAll(".catalyst-group__row")).toHaveLength(6);
  });

  it("flags held tickers and leads its category with them", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = snapshot([
      ...Array.from({ length: 8 }, (_, i) =>
        row({ type: "earnings", ticker: `T${i}`, title: `T${i} earnings`, date: "2026-08-04" }),
      ),
      row({ type: "earnings", ticker: "MCHP", title: "MCHP earnings", date: "2026-08-28" }),
    ]);
    render(<CatalystsQuadrant positionTickers={new Set(["MCHP"])} />);
    const section = sectionFor("EARNINGS");
    const firstRow = section.querySelector(".catalyst-group__row") as HTMLElement;
    expect(firstRow.textContent).toContain("MCHP · MCHP earnings");
    expect(within(firstRow).getByText("HELD")).toBeTruthy();
    expect(within(section).getByText("1 held")).toBeTruthy();
  });

  it("derives the rail from the payload", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = snapshot([row({ title: "Core CPI", date: "2026-08-07" })]);
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    const rail = document.querySelector(".panel-meta-rail");
    expect(rail?.textContent).toContain("1");
  });

  it("renders the empty state for a fossil snapshot", () => {
    freeze("2026-08-04T14:00:00Z");
    payload = {
      scan_time: "2026-07-01T10:30:00Z",
      count: 1,
      catalysts: [row({ title: "Old print", date: "2026-07-01" })],
    };
    render(<CatalystsQuadrant positionTickers={new Set()} />);
    expect(screen.getByText(/No upcoming catalysts/)).toBeTruthy();
  });
});
