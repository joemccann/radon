/**
 * @vitest-environment jsdom
 *
 * Surface pins for pack C: thinking wait on existing flow/GEX/agent waits,
 * Gate 01-04 beam only while evaluating, IB beam only when connected.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: (props: { state: string; size?: number }) => (
    <canvas data-testid="thinking-orb" data-state={props.state} data-size={String(props.size ?? 20)} />
  ),
}));

// Reflects the props EvaluatingBeam spreads from GATE_BEAM so the tests can
// assert them on the rendered element — deleting `{...GATE_BEAM}` reds this
// file (T-456).
vi.mock("border-beam", () => ({
  BorderBeam: ({
    children,
    active,
    size,
    colorVariant,
    staticColors,
    strength,
    borderRadius,
  }: {
    children: React.ReactNode;
    active?: boolean;
    size?: string;
    colorVariant?: string;
    staticColors?: boolean;
    strength?: number;
    borderRadius?: number;
  }) => (
    <div
      data-testid="evaluating-beam"
      data-beam-active={active === false ? "false" : "true"}
      data-size={String(size)}
      data-color-variant={String(colorVariant)}
      data-static-colors={String(staticColors)}
      data-strength={String(strength)}
      data-border-radius={String(borderRadius)}
    >
      {children}
    </div>
  ),
}));

vi.mock("@/lib/useTickerFlowReport", () => ({
  useTickerFlowReport: () => ({
    data: null,
    status: "scanning",
    error: null,
    refresh: () => {},
  }),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, isTablet: false, hasMounted: true }),
}));

const mockUseGex = vi.fn();
vi.mock("@/lib/useGex", () => ({
  useGex: () => mockUseGex(),
}));

vi.mock("@/lib/useMarketHours", () => ({
  MarketState: { OPEN: "OPEN", CLOSED: "CLOSED", EXTENDED: "EXTENDED" },
}));

afterEach(cleanup);

describe("FourGateChips", () => {
  it("beams only the evaluating gate — a failed Gate 03 stops beaming", async () => {
    const { FourGateChips } = await import("../components/fx/FourGateChips");
    render(
      <FourGateChips
        states={{ "01": "evaluating", "02": "cleared", "03": "failed", "04": "idle" }}
      />,
    );

    expect(screen.getByTestId("gate-chip-01").getAttribute("data-gate-state")).toBe("evaluating");
    expect(screen.getByTestId("gate-chip-01").closest("[data-beam-active]")?.getAttribute("data-beam-active")).toBe("true");
    expect(screen.getByTestId("gate-chip-02").closest("[data-beam-active]")).toBeNull();
    expect(screen.getByTestId("gate-chip-03").getAttribute("data-gate-state")).toBe("failed");
    expect(screen.getByTestId("gate-chip-03").closest("[data-beam-active]")).toBeNull();
    expect(screen.getByTestId("gate-chip-04").closest("[data-beam-active]")).toBeNull();
  });

  it("renders the four sequential gates from FOUR_GATES", async () => {
    const { FourGateChips } = await import("../components/fx/FourGateChips");
    render(
      <FourGateChips states={{ "01": "idle", "02": "idle", "03": "idle", "04": "idle" }} />,
    );

    const chips = screen.getByTestId("four-gate-chips");
    const expectedGates = [
      ["01", "Convexity"],
      ["02", "Edge"],
      ["03", "Risk"],
      ["04", "Naked Shorts"],
    ];
    for (const [id, name] of expectedGates) {
      expect(screen.getByTestId(`gate-chip-${id}`).textContent).toBe(`Gate ${id}${name}`);
    }
    expect(chips.querySelectorAll("[data-testid^='gate-chip-']")).toHaveLength(4);
  });

  it("spreads GATE_BEAM (mono, static, sm, calm strength) onto the beam", async () => {
    const { GATE_BEAM } = await import("../lib/librariesFx");
    const { FourGateChips } = await import("../components/fx/FourGateChips");
    render(
      <FourGateChips states={{ "01": "idle", "02": "evaluating", "03": "idle", "04": "idle" }} />,
    );

    const beam = screen.getByTestId("gate-chip-02").closest("[data-beam-active]");
    expect(beam?.getAttribute("data-color-variant")).toBe("mono");
    expect(beam?.getAttribute("data-static-colors")).toBe("true");
    expect(beam?.getAttribute("data-size")).toBe("sm");
    expect(beam?.getAttribute("data-strength")).toBe(String(GATE_BEAM.strength));
    expect(Number(beam?.getAttribute("data-strength"))).toBeLessThan(0.7);
    expect(beam?.getAttribute("data-border-radius")).toBe(String(GATE_BEAM.borderRadius));
  });
});

describe("ThinkingWait orb verbs", () => {
  it("maps each wait kind onto its orb verb at the render", async () => {
    const { default: ThinkingWait } = await import("../components/fx/ThinkingWait");
    const expectedVerbs = [
      ["flow", "searching"],
      ["gex", "weaving"],
      ["evaluate", "solving"],
      ["agent", "working"],
      ["compute", "composing"],
    ] as const;
    for (const [kind, verb] of expectedVerbs) {
      const { container, unmount } = render(<ThinkingWait kind={kind} label={`wait ${kind}`} />);
      const orb = container.querySelector("[data-testid='thinking-orb']");
      expect(orb?.getAttribute("data-state"), kind).toBe(verb);
      unmount();
    }
  });
});

describe("existing wait states", () => {
  it("shows a thinking wait on UW flow ingest", async () => {
    const TickerFlowReport = (await import("../components/flow-analysis/TickerFlowReport")).default;
    const { container } = render(<TickerFlowReport ticker="NVDA" />);
    const wait = container.querySelector('[data-testid="thinking-wait"]');
    expect(wait).not.toBeNull();
    expect(wait?.getAttribute("data-kind")).toBe("flow");
    expect(container.querySelector(".spectral-loader")).not.toBeNull();
  });

  it("shows a thinking wait on GEX rebuild", async () => {
    mockUseGex.mockReturnValue({
      data: null,
      loading: true,
      error: null,
      lastSync: null,
      syncing: false,
      syncNow: vi.fn(),
    });
    const GexPanel = (await import("../components/GexPanel")).default;
    const { container } = render(<GexPanel />);
    const wait = container.querySelector('[data-testid="thinking-wait"]');
    expect(wait).not.toBeNull();
    expect(wait?.getAttribute("data-kind")).toBe("gex");
    expect(container.textContent).toContain("Sampling gamma exposure by strike");
  });

  it("shows a thinking wait on a running engine-trace step", async () => {
    const EngineTrace = (await import("../components/agent/EngineTrace")).default;
    const { container } = render(
      <EngineTrace
        steps={[{ id: "phase-route", label: "Routing request", state: "running" }]}
      />,
    );
    const wait = container.querySelector('[data-testid="thinking-wait"]');
    expect(wait).not.toBeNull();
    expect(wait?.getAttribute("data-kind")).toBe("agent");
  });
});

describe("IB connected beam", () => {
  async function renderFooterWith(displayStatus: string) {
    vi.resetModules();
    vi.doMock("@/lib/IBStatusContext", () => ({
      useIBStatusContext: () => ({ displayStatus }),
    }));
    vi.doMock("@/lib/useServiceHealth", () => ({
      useServiceHealth: () => ({ data: null, loading: false, error: null }),
    }));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { default: FooterTelemetryStrip } = await import("../components/FooterTelemetryStrip");
    const view = render(<FooterTelemetryStrip />);
    return {
      ibBeam: screen.getByText("IB Gateway").closest("[data-testid='evaluating-beam']"),
      unmount: () => {
        view.unmount();
        vi.unstubAllGlobals();
      },
    };
  }

  it("wraps the IB chip in a GATE_BEAM-strength beam when connected", async () => {
    const { GATE_BEAM } = await import("../lib/librariesFx");
    const { ibBeam, unmount } = await renderFooterWith("connected");
    expect(ibBeam?.getAttribute("data-beam-active")).toBe("true");
    expect(ibBeam?.getAttribute("data-color-variant")).toBe("mono");
    expect(ibBeam?.getAttribute("data-strength")).toBe(String(GATE_BEAM.strength));
    unmount();
  });

  it("does not beam a degraded, offline, or demo control", async () => {
    for (const status of ["awaiting_2fa", "relay_offline", "demo"]) {
      const { ibBeam, unmount } = await renderFooterWith(status);
      expect(ibBeam, status).toBeNull();
      unmount();
    }
  });
});
