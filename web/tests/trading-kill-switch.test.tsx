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

  // T-101: Cancel All is a master global cancel (exit orders included) and
  // runAction() always posts {confirm:true}, so the ConfirmDialog is the ONLY
  // human gate. Pin that one click on the button fires nothing.
  describe("Cancel All confirm gate", () => {
    function postsTo(path: string) {
      return calls.filter((c) => c.method === "POST" && c.path === path);
    }

    it("clicking Cancel All Orders posts nothing until the dialog is confirmed", async () => {
      render(<TradingKillSwitch />);
      await waitFor(() => screen.getByTestId("trading-cancel-all-button"));

      fireEvent.click(screen.getByTestId("trading-cancel-all-button"));
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

      fireEvent.click(screen.getByTestId("admin-confirm-action"));

      await waitFor(() =>
        expect(postsTo("/api/admin/trading/cancel-all")).toHaveLength(1),
      );
      const cancelAll = postsTo("/api/admin/trading/cancel-all")[0];
      expect((cancelAll.body as Record<string, unknown>).confirm).toBe(true);
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    });

    it("dismissing the Cancel All dialog posts nothing", async () => {
      render(<TradingKillSwitch />);
      await waitFor(() => screen.getByTestId("trading-cancel-all-button"));

      fireEvent.click(screen.getByTestId("trading-cancel-all-button"));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(screen.getByTestId("admin-confirm-action")).toHaveProperty("disabled", true),
      );
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    });
  });

  describe("Halt confirm gate", () => {
    it("clicking Halt Trading posts nothing until the dialog is confirmed", async () => {
      render(<TradingKillSwitch />);
      await waitFor(() => screen.getByTestId("trading-halt-button"));

      fireEvent.click(screen.getByTestId("trading-halt-button"));
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

      fireEvent.click(screen.getByTestId("admin-confirm-action"));

      await waitFor(() =>
        expect(
          calls.filter((c) => c.method === "POST" && c.path === "/api/admin/trading/halt"),
        ).toHaveLength(1),
      );
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    });

    it("dismissing the Halt dialog posts nothing", async () => {
      render(<TradingKillSwitch />);
      await waitFor(() => screen.getByTestId("trading-halt-button"));

      fireEvent.click(screen.getByTestId("trading-halt-button"));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(screen.getByTestId("admin-confirm-action")).toHaveProperty("disabled", true),
      );
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    });
  });
});
