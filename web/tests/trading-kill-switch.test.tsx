/**
 * @vitest-environment jsdom
 *
 * REL-029 (R-053): the kill switch needs a UI affordance. Verifies the
 * <TradingKillSwitch /> admin card:
 *   - renders the polled halt state;
 *   - KILL is gated behind a type-to-confirm dialog (OrderRiskGate-class
 *     guardrail) and only then fires POST /api/admin/trading/kill with
 *     {confirm:true};
 *   - Resume appears while halted and fires the resume action.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TradingKillSwitch from "../components/admin/TradingKillSwitch";

type RecordedCall = { path: string; method: string; body: unknown };

let calls: RecordedCall[];
let halted: boolean;

function installFetchStub() {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost:3000").pathname;
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ path, method, body });
    const json = (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "content-type": "application/json" },
      });
    if (method === "GET" && path === "/api/admin/trading/status") {
      return json({ halted, reason: halted ? "kill switch" : null });
    }
    if (method === "POST" && path === "/api/admin/trading/kill") {
      halted = true;
      return json({ halted: true, cancel: { status: "ok", cancelled: 2 } });
    }
    if (method === "POST" && path === "/api/admin/trading/resume") {
      halted = false;
      return json({ halted: false });
    }
    if (method === "POST" && path === "/api/admin/trading/halt") {
      halted = true;
      return json({ halted: true });
    }
    if (method === "POST" && path === "/api/admin/trading/cancel-all") {
      return json({ status: "ok", cancelled: 2 });
    }
    return json({ error: "not found" }, 404);
  });
}

beforeEach(() => {
  calls = [];
  halted = false;
  installFetchStub();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("<TradingKillSwitch />", () => {
  it("renders the trading state from the status poll", async () => {
    render(<TradingKillSwitch />);
    await waitFor(() =>
      expect(screen.getByTestId("trading-halt-state").textContent?.toLowerCase()).toContain(
        "active",
      ),
    );
  });

  it("KILL only fires after the typed confirmation, with {confirm:true}", async () => {
    render(<TradingKillSwitch />);
    await waitFor(() => screen.getByTestId("trading-kill-button"));

    fireEvent.click(screen.getByTestId("trading-kill-button"));
    // Dialog open, nothing fired yet.
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

    const confirmBtn = screen.getByTestId("admin-confirm-action");
    expect(confirmBtn).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), {
      target: { value: "KILL" },
    });
    fireEvent.click(screen.getByTestId("admin-confirm-action"));

    await waitFor(() => {
      const kill = calls.find((c) => c.path === "/api/admin/trading/kill");
      expect(kill).toBeDefined();
      expect(kill?.method).toBe("POST");
      expect((kill?.body as Record<string, unknown>)?.confirm).toBe(true);
    });
  });

  it("shows Resume while halted and fires the resume action after confirm", async () => {
    halted = true;
    render(<TradingKillSwitch />);
    await waitFor(() =>
      expect(screen.getByTestId("trading-halt-state").textContent?.toLowerCase()).toContain(
        "halted",
      ),
    );

    fireEvent.click(screen.getByTestId("trading-resume-button"));
    fireEvent.click(screen.getByTestId("admin-confirm-action"));

    await waitFor(() => {
      const resume = calls.find((c) => c.path === "/api/admin/trading/resume");
      expect(resume?.method).toBe("POST");
    });
  });
});
