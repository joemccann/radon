/**
 * @vitest-environment jsdom
 *
 * TradingKillSwitch Halt / Resume choice is derived from an explicit status
 * machine (unknown | active | halted). While the status is unknown (first
 * probe pending, probe failed, or observation stale) neither Halt nor Resume
 * may be armed: the previous last-known value must not pick the action.
 *
 * Actions also route through the AdminWorkspace command lock when one is
 * provided, so a halt cannot run during a stack restart.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TradingKillSwitch from "../components/admin/TradingKillSwitch";

type Call = { url: string; method: string; body: unknown };

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

function haltResumeButtons(): HTMLButtonElement[] {
  return ["trading-halt-button", "trading-resume-button"]
    .map((id) => screen.queryByTestId(id) as HTMLButtonElement | null)
    .filter((b): b is HTMLButtonElement => b !== null);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("TradingKillSwitch status gate", () => {
  it("arms neither Halt nor Resume before the first status observation", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<TradingKillSwitch />);
    const buttons = haltResumeButtons();
    expect(buttons.length).toBeGreaterThan(0);
    for (const b of buttons) expect(b.disabled).toBe(true);
  });

  it("disarms Resume when a halted status goes unknown", async () => {
    vi.useFakeTimers();
    let fail = false;
    vi.stubGlobal("fetch", vi.fn(async () => (fail ? json({ error: "down" }, 503) : json({ halted: true, reason: "kill" }))));
    const settle = async () => { await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); }); };
    render(<TradingKillSwitch />);
    await settle();
    expect((screen.getByTestId("trading-resume-button") as HTMLButtonElement).disabled).toBe(false);

    fail = true;
    await act(async () => { vi.advanceTimersByTime(5_100); });
    await settle();
    expect(screen.getByTestId("trading-halt-state").textContent).toBe("Unknown");
    const buttons = haltResumeButtons();
    expect(buttons.length).toBeGreaterThan(0);
    for (const b of buttons) expect(b.disabled).toBe(true);
  });

  it("routes a confirmed halt through the provided command lock", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url: String(input), method, body: init?.body ? JSON.parse(String(init.body)) : null });
      return method === "GET" ? json({ halted: false }) : json({ halted: true });
    }));
    const runCommand = vi.fn(async <T,>(command: () => Promise<T>) => command());
    render(<TradingKillSwitch runCommand={runCommand} />);
    await waitFor(() => expect(screen.getByTestId("trading-halt-state").textContent).toBe("Active"));

    fireEvent.click(screen.getByTestId("trading-halt-button"));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("admin-confirm-action"));

    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/admin/trading/halt");
    expect(post?.body).toEqual({});
    expect(runCommand).toHaveBeenCalledTimes(1);
  });

  it("sends nothing and reports it when the command lock refuses", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url: String(input), method, body: null });
      return json({ halted: false });
    }));
    const runCommand = vi.fn(async () => false as const);
    render(<TradingKillSwitch runCommand={runCommand} />);
    await waitFor(() => expect(screen.getByTestId("trading-halt-state").textContent).toBe("Active"));

    fireEvent.click(screen.getByTestId("trading-halt-button"));
    fireEvent.click(screen.getByTestId("admin-confirm-action"));

    await waitFor(() => expect(runCommand).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.body.textContent).toContain("Another command is in progress"));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("disables every action while another admin command is pending", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ halted: false })));
    render(<TradingKillSwitch externalPending />);
    await waitFor(() => expect(screen.getByTestId("trading-halt-state").textContent).toBe("Active"));
    for (const id of ["trading-halt-button", "trading-cancel-all-button", "trading-kill-button"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
