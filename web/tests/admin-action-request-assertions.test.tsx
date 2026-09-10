/**
 * @vitest-environment jsdom
 *
 * The admin panel's destructive controls, asserted at the wire.
 *
 * Every control here stops production services. The existing admin tests cover
 * the GATES — that the confirm dialog opens, that the typed field is required,
 * that a `vi.fn()` prop was called — and none of them prove that a confirmed
 * click puts the right request on the wire. That is the same shape of hole that
 * let the chain ticket ship an armed Transmit button whose handler was closed
 * over a stale acknowledgement and silently sent nothing (PR #115): five tests,
 * all green, none asserting the request.
 *
 * So these tests render the REAL <AdminWorkspace /> — the component that owns
 * the fetch — record every call, and assert:
 *   1. nothing fires while the gate is still closed, and
 *   2. a confirmed action fires exactly ONE request, matched on the FULL path.
 *
 * Full-path matching is deliberate: `url.includes("/api/admin/services/")`
 * passes for the wrong unit and the wrong action, which is most of what could
 * actually go wrong here.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import AdminWorkspace from "../components/admin/AdminWorkspace";
import type { AdminHealthPayload, ServicesListResponse } from "../lib/adminTypes";

const GATEWAY_UNIT = "radon-ib-gateway.service";

const HEALTHY: AdminHealthPayload = {
  status: "ok",
  ib_gateway: {
    auth_state: "authenticated",
    port_listening: true,
    gateway_mode: "docker",
    host: "127.0.0.1",
    port: 4001,
    container_state: "running",
    container_health: "healthy",
    restart_backoff: {
      attempt_count: 0,
      last_attempt_at: 0,
      next_attempt_after: 0,
      next_attempt_in_secs: 0,
      last_outcome: null,
      push_lock: null,
    },
  },
  ib_pool: {
    sync: { connected: true, client_id: 3, managed_accounts: ["U1234"] },
  },
};

const SERVICES: ServicesListResponse = {
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

type RecordedCall = { url: string; method: string; cache?: RequestCache };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Records every request the workspace makes and answers the polls it needs to
 * render. Returns the recorder so a test can assert on the exact calls.
 */
function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET", cache: init?.cache });
      if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
        return jsonResponse(SERVICES);
      }
      if (init?.method === "POST") {
        return jsonResponse({ ok: true, detail: "done", returncode: 0 });
      }
      return jsonResponse(HEALTHY);
    }),
  );
  return calls;
}

/** POSTs only — the polls are noise for these assertions. */
function posts(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((c) => c.method === "POST");
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

async function renderWorkspace() {
  const calls = recordFetch();
  render(<AdminWorkspace />);
  await settle();
  return calls;
}

async function click(testId: string) {
  await act(async () => {
    fireEvent.click(screen.getByTestId(testId));
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("admin destructive actions reach the wire", () => {
  it("stops the gateway only after the typed gate, on the exact unit path", async () => {
    const calls = await renderWorkspace();

    await click("gateway-power-button");
    expect(screen.getByTestId("admin-confirm")).toBeTruthy();
    // The dialog is open. Nothing may have been sent yet.
    expect(posts(calls)).toHaveLength(0);

    // Confirm stays inert until the unit name is typed exactly.
    const confirmButton = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);

    await act(async () => {
      fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), {
        target: { value: GATEWAY_UNIT },
      });
      await Promise.resolve();
    });
    expect(posts(calls)).toHaveLength(0);

    await click("admin-confirm-action");

    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe(`/api/admin/services/${GATEWAY_UNIT}/stop`);
    expect(sent[0].method).toBe("POST");
    expect(sent[0].cache).toBe("no-store");
  });

  it("sends nothing when the gateway stop is cancelled", async () => {
    const calls = await renderWorkspace();

    await click("gateway-power-button");
    const cancel = screen
      .getByTestId("admin-confirm")
      .querySelector<HTMLButtonElement>(".admin-btn-ghost");
    await act(async () => {
      fireEvent.click(cancel!);
      for (let i = 0; i < 6; i += 1) await Promise.resolve();
    });

    expect(posts(calls)).toHaveLength(0);
  });

  it("restarts a service on the exact unit+action path, only after confirming", async () => {
    const calls = await renderWorkspace();

    await click("service-restart-radon-api.service");
    expect(screen.getByTestId("admin-confirm")).toBeTruthy();
    expect(posts(calls)).toHaveLength(0);

    await click("admin-confirm-action");

    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    // Full path: a wrong unit or a wrong action must fail this assertion.
    expect(sent[0].url).toBe("/api/admin/services/radon-api.service/restart");
    expect(sent[0].method).toBe("POST");
    expect(sent[0].cache).toBe("no-store");
  });

  it("sends no Gateway mutation when the app host has no remote control", async () => {
    const calls: RecordedCall[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url, method: init?.method ?? "GET", cache: init?.cache });
        if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
          return jsonResponse({
            ...SERVICES,
            host_role: "app",
            units: SERVICES.units.map((unit) =>
              unit.unit === GATEWAY_UNIT ? { ...unit, can_control: false } : unit,
            ),
          });
        }
        if (init?.method === "POST") {
          return jsonResponse({ ok: true, detail: "done", returncode: 0 });
        }
        return jsonResponse({ ...HEALTHY, host_role: "app" });
      }),
    );
    render(<AdminWorkspace />);
    await settle();

    expect(screen.queryByTestId("force-2fa-button")).toBeNull();
    expect(screen.queryByTestId("gateway-power-button")).toBeNull();
    expect(screen.getByTestId("gateway-broker-note").textContent).toMatch(/broker/i);
    expect(posts(calls)).toHaveLength(0);

    await click("restart-stack-button");
    expect(posts(calls)).toHaveLength(0);
    await click("admin-confirm-action");
    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/stack/restart");
    expect(sent.some((c) => c.url.includes("radon-ib-gateway"))).toBe(false);
    expect(sent.some((c) => c.url === "/api/admin/ib/restart")).toBe(false);
  });

  it("Force 2FA on an app host with remote control hits /api/admin/ib/restart", async () => {
    const calls: RecordedCall[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url, method: init?.method ?? "GET", cache: init?.cache });
        if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
          return jsonResponse({ ...SERVICES, supported: false, host_role: "app" });
        }
        if (init?.method === "POST") {
          return jsonResponse({ ok: true, detail: "done", returncode: 0, restarted: true });
        }
        return jsonResponse({ ...HEALTHY, host_role: "app" });
      }),
    );
    render(<AdminWorkspace />);
    await settle();

    expect(screen.getByTestId("force-2fa-button")).toBeTruthy();
    expect(screen.getByTestId("gateway-power-button")).toBeTruthy();
    expect(screen.queryByTestId("gateway-broker-note")).toBeNull();

    await click("force-2fa-button");
    expect(posts(calls)).toHaveLength(0);
    await click("admin-confirm-action");
    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/ib/restart");
    expect(sent[0].method).toBe("POST");
  });

  it("Reset Backoff hits /api/admin/ib/reset-backoff only after confirming (REL-172)", async () => {
    const calls = await renderWorkspace();

    await click("reset-backoff-button");
    expect(screen.getByTestId("admin-confirm")).toBeTruthy();
    expect(posts(calls)).toHaveLength(0);

    await click("admin-confirm-action");
    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/ib/reset-backoff");
    expect(sent[0].method).toBe("POST");
    expect(sent[0].cache).toBe("no-store");
  });

  it("on the app host the Reset Backoff gate says it releases the broker's lease (REL-172)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
          return jsonResponse({ ...SERVICES, supported: false, host_role: "app" });
        }
        return jsonResponse({ ...HEALTHY, host_role: "app" });
      }),
    );
    render(<AdminWorkspace />);
    await settle();

    await click("reset-backoff-button");
    expect(screen.getByTestId("admin-confirm").textContent).toMatch(/broker/i);
  });

  it("a broker transition disarms Force 2FA and Start on the app host (REL-172)", async () => {
    const calls: RecordedCall[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url, method: init?.method ?? "GET", cache: init?.cache });
        if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
          return jsonResponse({
            ...SERVICES,
            supported: false,
            host_role: "app",
            units: SERVICES.units.map((unit) =>
              unit.unit === GATEWAY_UNIT
                ? { ...unit, load_state: "remote", active_state: "activating", sub_state: "transition-pending" }
                : unit,
            ),
          });
        }
        return jsonResponse({
          ...HEALTHY,
          host_role: "app",
          ib_gateway: {
            ...HEALTHY.ib_gateway,
            port_listening: false,
            auth_state: "unreachable",
            restart_backoff: {
              ...HEALTHY.ib_gateway.restart_backoff!,
              push_lock: {
                holder: "broker:transition-pending",
                acquired_at: 0,
                expires_at: 0,
                remaining_secs: 60,
                reason: "broker transition pending",
              },
            },
          },
        });
      }),
    );
    render(<AdminWorkspace />);
    await settle();

    expect((screen.getByTestId("force-2fa-button") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("gateway-power-button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("gateway-power-disabled-reason").textContent).toMatch(/transition/i);
    await click("force-2fa-button");
    await click("gateway-power-button");
    expect(posts(calls)).toHaveLength(0);
  });

  it("Start Gateway hits the gateway start path, not stack restart", async () => {
    const calls: RecordedCall[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        calls.push({ url, method: init?.method ?? "GET", cache: init?.cache });
        if (url.endsWith("/api/admin/services") && (init?.method ?? "GET") === "GET") {
          return jsonResponse({
            ...SERVICES,
            supported: false,
            host_role: "app",
            units: SERVICES.units.map((unit) =>
              unit.unit === GATEWAY_UNIT
                ? { ...unit, active_state: "inactive", sub_state: "dead", can_control: true }
                : unit,
            ),
          });
        }
        if (init?.method === "POST") {
          return jsonResponse({ ok: true, detail: "done", returncode: 0 });
        }
        return jsonResponse({
          ...HEALTHY,
          host_role: "app",
          ib_gateway: { ...HEALTHY.ib_gateway, port_listening: false, auth_state: "unreachable" },
        });
      }),
    );
    render(<AdminWorkspace />);
    await settle();

    expect(screen.getByTestId("gateway-power-button").textContent).toBe("Start Gateway");
    await click("gateway-power-button");
    expect(posts(calls)).toHaveLength(0);
    await click("admin-confirm-action");
    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe(`/api/admin/services/${GATEWAY_UNIT}/start`);
    expect(sent[0].method).toBe("POST");
    expect(sent[0].cache).toBe("no-store");
    expect(sent.some((c) => c.url === "/api/admin/stack/restart")).toBe(false);
  });

  it("stops a service on the stop path, not the restart path", async () => {
    const calls = await renderWorkspace();

    await click("service-stop-radon-api.service");
    expect(posts(calls)).toHaveLength(0);

    await click("admin-confirm-action");

    const sent = posts(calls);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/admin/services/radon-api.service/stop");
  });
});
