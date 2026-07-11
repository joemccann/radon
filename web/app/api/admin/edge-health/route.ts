import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { filterApplicableServiceHealthRows } from "@/lib/serviceHealthApplicability";

// Proxy to the isolated health daemon's aggregate (/edge-health/status — units,
// probes, service_health rows). Reuses the always-up off-box surface rather than
// hitting radon-api, so the admin reliability sections keep reporting even when
// the trading stack is down. Never static-cached (cache contract).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// On the VPS the daemon is loopback (127.0.0.1:8330/status); for laptop dev the
// public edge (app.radon.run/edge-health/status) is reachable. Override via env.
const EDGE_HEALTH_URL =
  process.env.RADON_EDGE_HEALTH_URL || "https://app.radon.run/edge-health/status";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const res = await fetch(EDGE_HEALTH_URL, { cache: "no-store", signal: controller.signal });
    if (!res.ok) {
      // /edge-health/status is always-200 by contract; a non-200 means the edge
      // or daemon is down. Report it as a payload flag, never a 4xx/5xx.
      return setNoStoreResponseHeaders(
        NextResponse.json({ reachable: false, status: res.status }),
        requestId,
      );
    }
    const data = await res.json() as Record<string, unknown>;
    const serviceHealth = data.service_health;
    if (typeof serviceHealth === "object" && serviceHealth !== null) {
      const section = serviceHealth as Record<string, unknown>;
      if (Array.isArray(section.rows)) {
        const rows = filterApplicableServiceHealthRows(section.rows);
        data.service_health = { ...section, rows, row_count: rows.length };
      }
    }
    return setNoStoreResponseHeaders(NextResponse.json({ reachable: true, ...data }), requestId);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "edge-health fetch failed";
    return setNoStoreResponseHeaders(NextResponse.json({ reachable: false, error: detail }), requestId);
  } finally {
    clearTimeout(timer);
  }
}
