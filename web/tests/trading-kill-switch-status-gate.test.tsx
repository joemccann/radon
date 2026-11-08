/**
 * @vitest-environment jsdom
 *
 * TradingKillSwitch Halt / Resume choice is derived from an explicit status
 * machine (unknown | active | halted). Resume is armed only on a fresh
 * `halted` observation. Halt is the fail-safe direction and idempotent
 * server-side, so it stays armed while the status is unknown (first probe
 * pending, probe failed, or observation stale); only a fresh `halted`
 * observation withdraws it.
 *
 * Emergency actions never wait on AdminWorkspace's shared command lock: a
 * stack restart or 2FA wait must not disarm Halt / Cancel All / Kill.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TradingKillSwitch from "../components/admin/TradingKillSwitch";

type Call = { url: string; method: string; body: unknown };

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

function button(id: string): HTMLButtonElement {
  return screen.getByTestId(id) as HTMLButtonElement;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("TradingKillSwitch status gate", () => {
  it("arms Halt but not Resume before the first status observation", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<TradingKillSwitch />);
    expect(button("trading-halt-button").disabled).toBe(false);
    expect(button("trading-resume-button").disabled).toBe(true);
  });

  it("disarms Resume and offers Halt when a halted status goes unknown", async () => {
    vi.useFakeTimers();
    let fail = false;
    vi.stubGlobal("fetch", vi.fn(async () => (fail ? json({ error: "down" }, 503) : json({ halted: true, reason: "kill" }))));
    const settle = async () => { await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); }); };
    render(<TradingKillSwitch />);
    await settle();
    expect(button("trading-resume-button").disabled).toBe(false);
    expect(screen.queryByTestId("trading-halt-button")).toBeNull();

    fail = true;
    await act(async () => { vi.advanceTimersByTime(5_100); });
    await settle();
    expect(screen.getByTestId("trading-halt-state").textContent).toBe("Unknown");
    expect(button("trading-resume-button").disabled).toBe(true);
    expect(button("trading-halt-button").disabled).toBe(false);
  });

  it("sends a confirmed halt while the status probe is failing", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url: String(input), method, body: init?.body ? JSON.parse(String(init.body)) : null });
      return method === "GET" ? json({ error: "down" }, 503) : json({ halted: true });
    }));
    render(<TradingKillSwitch />);
    await waitFor(() => expect(calls.some((c) => c.method === "GET")).toBe(true));
    expect(screen.getByTestId("trading-halt-state").textContent).toBe("Unknown");

    fireEvent.click(button("trading-halt-button"));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("admin-confirm-action"));

    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/admin/trading/halt");
    expect(post?.method).toBe("POST");
    expect(post?.body).toEqual({});
  });
});
