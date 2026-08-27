/**
 * @vitest-environment jsdom
 *
 * Behavioral coverage for the admin panel's polling + row-flash UX.
 * Specifically pins down:
 *   - <AdminWorkspace> polls /api/admin/services every 5s on a steady
 *     state and tears the interval down on unmount.
 *   - A successful service action flashes the matching row with the
 *     ``admin-row-flash`` class for ~2s, then clears it.
 *   - A failed service action uses the error-tone class instead.
 *   - Row activity text reflects uptime / last-run timestamps.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import AdminWorkspace from "../components/admin/AdminWorkspace";
import ServiceControlPanel from "../components/admin/ServiceControlPanel";
import type { FlashTarget } from "../components/admin/AdminWorkspace";
import type {
  AdminHealthPayload,
  ServicesListResponse,
} from "../lib/adminTypes";

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
      unit: "radon-cta-sync.service",
      load_state: "loaded",
      active_state: "inactive",
      sub_state: "dead",
      description: "MenthorQ CTA sync",
      can_control: true,
      last_active_at: "2026-05-19T11:55:00Z",
      last_exit_code: 0,
    },
    {
      unit: "radon-api.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon FastAPI",
      can_control: true,
      uptime_secs: 3 * 3600 + 22 * 60,
    },
  ],
};

function stubFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    return impl(url, init);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

beforeEach(() => {
  // useViewport reads window.matchMedia on mount; jsdom doesn't ship one.
  if (typeof window.matchMedia !== "function") {
    Object.defineProperty(window, "matchMedia", {
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
      configurable: true,
    });
  }
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("<AdminWorkspace /> services polling", () => {
  it("schedules a services poll every 5s and clears it on unmount", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch((url) => {
      if (url.endsWith("/api/admin/services")) return jsonResponse(SERVICES);
      return jsonResponse(HEALTHY);
    });

    const { unmount } = render(<AdminWorkspace />);

    // Flush the initial fetches scheduled in useEffect mount.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const initialCount = fetchMock.mock.calls.filter(
      (call) => String(call[0]).endsWith("/api/admin/services"),
    ).length;
    expect(initialCount).toBeGreaterThanOrEqual(1);

    // Advance just past the 5s services poll boundary.
    await act(async () => {
      vi.advanceTimersByTime(5_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    const afterTick = fetchMock.mock.calls.filter(
      (call) => String(call[0]).endsWith("/api/admin/services"),
    ).length;
    expect(afterTick).toBeGreaterThan(initialCount);

    unmount();

    // After unmount, advancing time must NOT fire further /services calls.
    const before = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    const after = fetchMock.mock.calls.length;
    expect(after).toBe(before);
  });
});

describe("<ServiceControlPanel /> flash target", () => {
  it("applies admin-row-flash to the matched unit when ok", () => {
    const flashTarget: FlashTarget = { unit: "radon-api.service", at: Date.now(), ok: true };
    render(
      <ServiceControlPanel
        services={SERVICES}
        loading={false}
        error={null}
        onAction={vi.fn()}
        flashTarget={flashTarget}
      />,
    );
    const row = screen.getByTestId("service-row-radon-api.service");
    expect(row.className).toContain("admin-row-flash");
    expect(row.className).not.toContain("admin-row-flash-error");
    expect(row.getAttribute("data-flash")).toBe("true");
  });

  it("applies admin-row-flash-error when the action failed", () => {
    const flashTarget: FlashTarget = { unit: "radon-api.service", at: Date.now(), ok: false };
    render(
      <ServiceControlPanel
        services={SERVICES}
        loading={false}
        error={null}
        onAction={vi.fn()}
        flashTarget={flashTarget}
      />,
    );
    const row = screen.getByTestId("service-row-radon-api.service");
    expect(row.className).toContain("admin-row-flash-error");
  });

  it("does NOT flash a non-matching row", () => {
    const flashTarget: FlashTarget = { unit: "radon-api.service", at: Date.now(), ok: true };
    render(
      <ServiceControlPanel
        services={SERVICES}
        loading={false}
        error={null}
        onAction={vi.fn()}
        flashTarget={flashTarget}
      />,
    );
    const other = screen.getByTestId("service-row-radon-cta-sync.service");
    expect(other.className).not.toContain("admin-row-flash");
    expect(other.className).not.toContain("admin-row-flash-error");
  });

  it("clears the flash class when flashTarget is null", () => {
    render(
      <ServiceControlPanel
        services={SERVICES}
        loading={false}
        error={null}
        onAction={vi.fn()}
        flashTarget={null}
      />,
    );
    const row = screen.getByTestId("service-row-radon-api.service");
    expect(row.className).not.toContain("admin-row-flash");
  });

  it("renders the activity label for each unit", () => {
    render(
      <ServiceControlPanel
        services={SERVICES}
        loading={false}
        error={null}
        onAction={vi.fn()}
        flashTarget={null}
      />,
    );
    expect(
      screen.getByTestId("service-activity-radon-api.service").textContent,
    ).toMatch(/^running \d/);
    expect(
      screen.getByTestId("service-activity-radon-cta-sync.service").textContent,
    ).toContain("last ran");
  });
});

describe("<AdminWorkspace /> flash lifecycle", () => {
  it("flashes the row on successful action and clears after the timer fires", async () => {
    vi.useFakeTimers();
    // Matched on the FULL path, not a `includes("/api/admin/services/")`
    // prefix: a wrong unit or a wrong action segment must fail this test
    // rather than satisfy it.
    const startedUnits: string[] = [];
    stubFetch((url, init) => {
      if (url.endsWith("/api/admin/services") && (!init || init.method === undefined)) {
        return jsonResponse(SERVICES);
      }
      if (url === "/api/admin/services/radon-cta-sync.service/start" && init?.method === "POST") {
        startedUnits.push(url);
        return jsonResponse({ ok: true, detail: "started", returncode: 0 });
      }
      return jsonResponse(HEALTHY);
    });

    render(<AdminWorkspace />);

    // Wait for initial services list to land.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const startBtn = screen.getByTestId("service-start-radon-cta-sync.service");
    await act(async () => {
      fireEvent.click(startBtn);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(startedUnits).toEqual(["/api/admin/services/radon-cta-sync.service/start"]);
    const row = screen.getByTestId("service-row-radon-cta-sync.service");
    expect(row.className).toContain("admin-row-flash");

    // Flash should clear after FLASH_DURATION_MS (2s).
    await act(async () => {
      vi.advanceTimersByTime(2_100);
    });
    expect(row.className).not.toContain("admin-row-flash");
  });

  it("flashes error tone when the service action fails", async () => {
    vi.useFakeTimers();
    stubFetch((url, init) => {
      if (url.endsWith("/api/admin/services") && (!init || init.method === undefined)) {
        return jsonResponse(SERVICES);
      }
      if (url.includes("/api/admin/services/") && init?.method === "POST") {
        return jsonResponse({ error: "boom" }, { status: 500 });
      }
      return jsonResponse(HEALTHY);
    });

    render(<AdminWorkspace />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("service-start-radon-cta-sync.service"));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const row = screen.getByTestId("service-row-radon-cta-sync.service");
    expect(row.className).toContain("admin-row-flash-error");
    expect(row.className).not.toMatch(/admin-row-flash(?!-error)/);
  });
});
