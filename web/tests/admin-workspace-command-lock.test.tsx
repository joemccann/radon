/**
 * @vitest-environment jsdom
 *
 * AdminWorkspace command lock and post-action refresh, at the wire.
 *
 * - Trading controls share the workspace command lock: while a service
 *   command is in flight, Halt / Cancel All / Kill are disarmed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import AdminWorkspace from "../components/admin/AdminWorkspace";
import type { AdminHealthPayload, ServicesListResponse } from "../lib/adminTypes";

const HEALTHY: AdminHealthPayload = {
  status: "ok",
  ib_gateway: { auth_state: "authenticated", port_listening: true },
  ib_pool: { sync: { connected: true, client_id: 3, managed_accounts: ["U1234"] } },
};

const SERVICES: ServicesListResponse = {
  supported: true,
  units: [
    {
      unit: "radon-api.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon FastAPI",
      can_control: true,
      uptime_secs: 3_600,
    },
  ],
};

type Call = { url: string; method: string };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

async function click(testId: string) {
  await act(async () => {
    fireEvent.click(screen.getByTestId(testId));
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("AdminWorkspace command lock", () => {
  it("disarms trading controls while a service command is in flight", async () => {
    const calls: Call[] = [];
    let releaseRestart: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "POST" && url === "/api/admin/services/radon-api.service/restart") {
        return new Promise<Response>((resolve) => { releaseRestart = resolve; });
      }
      if (url === "/api/admin/trading/status") return Promise.resolve(json({ halted: false }));
      if (url === "/api/admin/services") return Promise.resolve(json(SERVICES));
      return Promise.resolve(json(HEALTHY));
    }));
    render(<AdminWorkspace />);
    await settle();
    expect((screen.getByTestId("trading-kill-button") as HTMLButtonElement).disabled).toBe(false);

    await click("service-restart-radon-api.service");
    await click("admin-confirm-action");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/api/admin/services/radon-api.service/restart",
    ]);
    for (const id of ["trading-halt-button", "trading-cancel-all-button", "trading-kill-button"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true);
    }

    await act(async () => { releaseRestart(json({ ok: true, detail: "done" })); });
    await settle();
    expect((screen.getByTestId("trading-kill-button") as HTMLButtonElement).disabled).toBe(false);
  });
});
