/**
 * @vitest-environment jsdom
 *
 * AdminWorkspace command lock and post-action refresh, at the wire.
 *
 * - Emergency trading controls never wait on the workspace command lock:
 *   while a service command is in flight, Kill still reaches the wire.
 * - A command the lock refuses is logged, never dropped silently.
 * - A post-action refresh queues behind an in-flight poll instead of being
 *   dropped, and a unit turning transitional retunes only the services poll.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

const GATEWAY_UNIT = "radon-ib-gateway.service";

const SERVICES_WITH_GATEWAY: ServicesListResponse = {
  supported: true,
  units: [
    {
      unit: GATEWAY_UNIT,
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "IB Gateway",
      can_control: true,
      uptime_secs: 7_200,
    },
    ...SERVICES.units,
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
  it("keeps emergency trading controls armed while a service command is in flight", async () => {
    const calls: Array<Call & { body: unknown }> = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (method === "POST" && url === "/api/admin/services/radon-api.service/restart") {
        return new Promise<Response>(() => {});
      }
      if (method === "POST" && url === "/api/admin/trading/kill") return Promise.resolve(json({ halted: true }));
      if (url === "/api/admin/trading/status") return Promise.resolve(json({ halted: false }));
      if (url === "/api/admin/services") return Promise.resolve(json(SERVICES));
      return Promise.resolve(json(HEALTHY));
    }));
    render(<AdminWorkspace />);
    await settle();

    await click("service-restart-radon-api.service");
    await click("admin-confirm-action");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/api/admin/services/radon-api.service/restart",
    ]);
    for (const id of ["trading-halt-button", "trading-cancel-all-button", "trading-kill-button"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(false);
    }

    await click("trading-kill-button");
    const killDialog = screen.getAllByTestId("admin-confirm").find((d) => d.textContent?.includes("Fire the kill switch?"))!;
    fireEvent.change(within(killDialog).getByTestId("admin-confirm-typed-input"), { target: { value: "KILL" } });
    await act(async () => {
      fireEvent.click(within(killDialog).getByTestId("admin-confirm-action"));
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });

    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => c.url)).toEqual([
      "/api/admin/services/radon-api.service/restart",
      "/api/admin/trading/kill",
    ]);
    expect(posts[1].body).toEqual({ confirm: true });
  });

  it("logs a command the lock refused instead of dropping it silently", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "POST") return new Promise<Response>(() => {});
      if (url === "/api/admin/services") return Promise.resolve(json(SERVICES_WITH_GATEWAY));
      return Promise.resolve(json(HEALTHY));
    }));
    render(<AdminWorkspace />);
    await settle();

    // Two confirmations open at once: the gateway Stop and a service restart.
    await click("gateway-power-button");
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: GATEWAY_UNIT } });
    await click("service-restart-radon-api.service");
    const dialogs = screen.getAllByTestId("admin-confirm");
    const stopDialog = dialogs.find((d) => d.textContent?.includes("Stop the IB Gateway?"))!;
    const restartDialog = dialogs.find((d) => d !== stopDialog && d.textContent?.includes("radon-api.service"))!;

    // Both confirmed in one tick: the second hits the held lock.
    await act(async () => {
      fireEvent.click(within(restartDialog).getByTestId("admin-confirm-action"));
      fireEvent.click(within(stopDialog).getByTestId("admin-confirm-action"));
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });

    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/api/admin/services/radon-api.service/restart",
    ]);
    expect(document.body.textContent).toContain(`${GATEWAY_UNIT} stop`);
    expect(document.body.textContent).toContain("Not sent. Another command is in progress.");
  });
});

describe("AdminWorkspace polling", () => {
  function countOf(calls: Call[], url: string) {
    return calls.filter((c) => c.method === "GET" && c.url === url).length;
  }

  it("re-fetches services after an action even when a poll was in flight", async () => {
    vi.useFakeTimers();
    const calls: Call[] = [];
    let servicesGets = 0;
    let releasePoll: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (method === "POST") return Promise.resolve(json({ ok: true, detail: "done" }));
      if (url === "/api/admin/services") {
        servicesGets += 1;
        if (servicesGets === 2) return new Promise<Response>((resolve) => { releasePoll = resolve; });
        return Promise.resolve(json(SERVICES));
      }
      return Promise.resolve(json(HEALTHY));
    }));
    render(<AdminWorkspace />);
    await settle();
    await act(async () => { vi.advanceTimersByTime(5_000); });
    await settle();
    expect(countOf(calls, "/api/admin/services")).toBe(2);

    await click("service-restart-radon-api.service");
    await click("admin-confirm-action");
    await settle();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([
      "/api/admin/services/radon-api.service/restart",
    ]);

    // The poll that started before the action answers; the post-action
    // refresh must still run afterwards.
    await act(async () => { releasePoll(json(SERVICES)); });
    await settle();
    expect(countOf(calls, "/api/admin/services")).toBe(3);
  });

  it("a unit turning transitional retunes the services poll without refetching every source", async () => {
    vi.useFakeTimers();
    const calls: Call[] = [];
    let servicesGets = 0;
    const transitional: ServicesListResponse = {
      ...SERVICES,
      units: [{ ...SERVICES.units[0], active_state: "activating", sub_state: "start" }],
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/admin/services") {
        servicesGets += 1;
        return Promise.resolve(json(servicesGets >= 2 ? transitional : SERVICES));
      }
      return Promise.resolve(json(HEALTHY));
    }));
    render(<AdminWorkspace />);
    await settle();
    expect(countOf(calls, "/api/admin/health")).toBe(1);
    expect(countOf(calls, "/api/admin/reliability")).toBe(1);

    await act(async () => { vi.advanceTimersByTime(5_000); });
    await settle();
    // One scheduled health poll; the transitional flip adds no burst.
    expect(countOf(calls, "/api/admin/health")).toBe(2);
    expect(countOf(calls, "/api/admin/reliability")).toBe(1);
    expect(countOf(calls, "/api/admin/services")).toBe(2);

    await act(async () => { vi.advanceTimersByTime(2_000); });
    await settle();
    expect(countOf(calls, "/api/admin/services")).toBe(3);
  });
});
