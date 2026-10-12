/**
 * @vitest-environment jsdom
 *
 * Progressive log disclosure on /admin. A Writer Freshness row in ERROR or
 * STALE, and a Service Control row whose unit is failed, expands to show the
 * journal lines for the owning systemd unit.
 *
 * The log fetch sits behind a gate (the row must be expanded), so it is
 * asserted at the wire: fetch is stubbed, the component that OWNS the fetch is
 * rendered, the full URL + method are matched, and nothing may fire while the
 * row is still collapsed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import WriterFreshnessTable from "../components/admin/WriterFreshnessTable";
import ServiceControlPanel from "../components/admin/ServiceControlPanel";
import ServiceLogDisclosure from "../components/admin/ServiceLogDisclosure";
import type { ServiceHealthRow, ServicesListResponse } from "../lib/adminTypes";

type RecordedCall = { url: string; method: string };

let calls: RecordedCall[] = [];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const LOGS_OK = {
  service: "dropbox-research",
  unit: "radon-dropbox-research.service",
  available: true,
  reason: null,
  truncated: false,
  entries: [
    { ts: "2026-10-12T13:00:00.000000Z", priority: 6, message: "Starting dropbox research sync" },
    { ts: "2026-10-12T13:00:01.000000Z", priority: 4, message: "rate limited, retrying" },
    { ts: "2026-10-12T13:00:02.000000Z", priority: 3, message: "Traceback: KeyError 'cursor'" },
    { ts: "2026-10-12T13:00:03.000000Z", priority: 6, message: "Finished with errors" },
  ],
};

function stubFetch(body: unknown = LOGS_OK, status = 200) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: (init?.method ?? "GET").toUpperCase() });
    return jsonResponse(body, status);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  calls = [];
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const NOW_ISO = () => new Date().toISOString();
const DAYS_AGO_ISO = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();

function errorRow(): ServiceHealthRow {
  return {
    service: "dropbox-research",
    state: "error",
    updated_at: NOW_ISO(),
    last_attempt_started_at: "2026-10-12T13:00:00Z",
    last_attempt_finished_at: "2026-10-12T13:00:03Z",
    last_error: "KeyError: 'cursor' in sync_folder()",
  };
}

function staleRow(): ServiceHealthRow {
  return { service: "db-backup", state: "ok", updated_at: DAYS_AGO_ISO(10), last_error: null };
}

function freshRow(): ServiceHealthRow {
  return { service: "db-backup", state: "ok", updated_at: NOW_ISO(), last_error: null };
}

describe("WriterFreshnessTable disclosure", () => {
  it("error row is expandable and toggles aria-expanded false -> true", async () => {
    stubFetch();
    render(<WriterFreshnessTable reachable rows={[errorRow()]} />);
    const row = screen.getByTestId("writer-row-dropbox-research");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("service-log-disclosure")).toBeNull();
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("service-log-disclosure")).toBeTruthy();
    await screen.findByText("Traceback: KeyError 'cursor'");
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("service-log-disclosure")).toBeNull();
  });

  it("stale row is expandable through its disclosure button", async () => {
    stubFetch({ ...LOGS_OK, service: "db-backup", unit: "radon-db-backup.service" });
    render(<WriterFreshnessTable reachable rows={[staleRow()]} />);
    const row = screen.getByTestId("writer-row-db-backup");
    expect(row.textContent).toContain("STALE");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    const toggle = screen.getByRole("button", { name: /show logs for db-backup/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("service-log-disclosure")).toBeTruthy();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/admin/logs?service=db-backup", method: "GET" });
  });

  it("ok / fresh row has no disclosure and does not react to clicks", () => {
    const fetchFn = stubFetch();
    render(<WriterFreshnessTable reachable rows={[freshRow()]} />);
    const row = screen.getByTestId("writer-row-db-backup");
    expect(row.hasAttribute("aria-expanded")).toBe(false);
    expect(screen.queryByRole("button", { name: /show logs/i })).toBeNull();
    fireEvent.click(row);
    expect(screen.queryByTestId("service-log-disclosure")).toBeNull();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("disclosure detail row spans every column", () => {
    stubFetch();
    render(<WriterFreshnessTable reachable rows={[errorRow()]} />);
    fireEvent.click(screen.getByTestId("writer-row-dropbox-research"));
    const cell = screen.getByTestId("service-log-disclosure").closest("td");
    expect(cell?.getAttribute("colspan")).toBe("5");
  });

  it("wire: fetches nothing while collapsed, then exactly GET /api/admin/logs?service=dropbox-research", async () => {
    stubFetch();
    render(<WriterFreshnessTable reachable rows={[errorRow(), freshRow()]} />);
    await act(async () => {});
    expect(calls).toEqual([]);
    fireEvent.click(screen.getByTestId("writer-row-dropbox-research"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/admin/logs?service=dropbox-research", method: "GET" });
  });
});

describe("ServiceControlPanel disclosure", () => {
  function services(): ServicesListResponse {
    return {
      supported: true,
      units: [
        {
          unit: "radon-api.service",
          load_state: "loaded",
          active_state: "active",
          sub_state: "running",
          description: "Radon FastAPI",
          can_control: true,
        },
        {
          unit: "radon-cri-scan.service",
          load_state: "loaded",
          active_state: "failed",
          sub_state: "failed",
          description: "CRI scan",
          can_control: true,
          last_exit_code: 1,
        },
      ],
    };
  }

  it("wire: failed unit fetches nothing until expanded, then GET /api/admin/logs?unit=radon-cri-scan.service", async () => {
    stubFetch({ ...LOGS_OK, service: null, unit: "radon-cri-scan.service" });
    render(<ServiceControlPanel services={services()} loading={false} error={null} onAction={vi.fn()} />);
    await act(async () => {});
    expect(calls).toEqual([]);
    const row = screen.getByTestId("service-row-radon-cri-scan.service");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ url: "/api/admin/logs?unit=radon-cri-scan.service", method: "GET" });
    expect(screen.getByTestId("service-log-disclosure").closest("td")?.getAttribute("colspan")).toBe("4");
  });

  it("running unit has no disclosure", () => {
    stubFetch();
    render(<ServiceControlPanel services={services()} loading={false} error={null} onAction={vi.fn()} />);
    const row = screen.getByTestId("service-row-radon-api.service");
    expect(row.hasAttribute("aria-expanded")).toBe(false);
    fireEvent.click(row);
    expect(screen.queryByTestId("service-log-disclosure")).toBeNull();
    expect(calls).toEqual([]);
  });

  it("clicking a control button on a failed row does not toggle the disclosure", () => {
    stubFetch();
    render(<ServiceControlPanel services={services()} loading={false} error={null} onAction={vi.fn().mockResolvedValue(undefined)} />);
    fireEvent.click(screen.getByTestId("service-restart-radon-cri-scan.service"));
    expect(screen.getByTestId("service-row-radon-cri-scan.service").getAttribute("aria-expanded")).toBe("false");
    expect(calls).toEqual([]);
  });
});

describe("<ServiceLogDisclosure />", () => {
  it("renders the raw last_error in full and the unit name", async () => {
    stubFetch();
    const longError = `KeyError: 'cursor' ${"x".repeat(400)} END`;
    render(<ServiceLogDisclosure service="dropbox-research" lastError={longError} />);
    expect(screen.getByTestId("service-log-raw-error").textContent).toBe(longError);
    await screen.findByText("radon-dropbox-research.service");
  });

  it("styles priority<=3 as error and 4 as warning", async () => {
    stubFetch();
    render(<ServiceLogDisclosure service="dropbox-research" />);
    const err = await screen.findByText("Traceback: KeyError 'cursor'");
    expect(err.closest("li")?.className).toContain("admin-journal-entry-negative");
    const warn = screen.getByText("rate limited, retrying");
    expect(warn.closest("li")?.className).toContain("admin-journal-entry-warning");
  });

  it("Errors only defaults ON when warnings exist and can be turned off", async () => {
    stubFetch();
    render(<ServiceLogDisclosure service="dropbox-research" />);
    await screen.findByText("Traceback: KeyError 'cursor'");
    const toggle = screen.getByRole("checkbox", { name: /errors only/i }) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    expect(screen.queryByText("Starting dropbox research sync")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    expect(screen.getByText("Starting dropbox research sync")).toBeTruthy();
  });

  it("Errors only defaults OFF when no warning-or-worse entry exists", async () => {
    stubFetch({
      ...LOGS_OK,
      entries: [{ ts: "2026-10-12T13:00:00Z", priority: 6, message: "all quiet" }],
    });
    render(<ServiceLogDisclosure service="dropbox-research" />);
    await screen.findByText("all quiet");
    expect((screen.getByRole("checkbox", { name: /errors only/i }) as HTMLInputElement).checked).toBe(false);
  });

  it("marks entries at or after lastRunIso as this run", async () => {
    stubFetch();
    render(<ServiceLogDisclosure service="dropbox-research" lastRunIso="2026-10-12T13:00:02Z" />);
    const err = await screen.findByText("Traceback: KeyError 'cursor'");
    expect(err.closest("li")?.textContent).toContain("this run");
    const warn = screen.getByText("rate limited, retrying");
    expect(warn.closest("li")?.textContent).not.toContain("this run");
  });

  it("shows the reason when the journal is unavailable", async () => {
    stubFetch({
      service: "dropbox-research",
      unit: "radon-dropbox-research.service",
      available: false,
      reason: "Journal not readable on this host.",
      entries: [],
      truncated: false,
    });
    render(<ServiceLogDisclosure service="dropbox-research" />);
    await screen.findByText("Journal not readable on this host.");
  });

  it("shows an error when the fetch fails", async () => {
    stubFetch({ error: { message: "upstream down" } }, 502);
    render(<ServiceLogDisclosure service="dropbox-research" />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => /logs could not be loaded/i.test(a.textContent ?? ""))).toBe(true);
  });

  it("shows a loading skeleton before the response lands", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<ServiceLogDisclosure unit="radon-cri-scan.service" />);
    expect(screen.getByTestId("service-log-loading")).toBeTruthy();
    expect(screen.getByText("radon-cri-scan.service")).toBeTruthy();
  });
});
