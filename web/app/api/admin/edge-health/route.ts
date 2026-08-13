import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { filterApplicableServiceHealthRows } from "@/lib/serviceHealthApplicability";

// Proxy to the isolated health daemon's aggregate (/status — units, probes,
// service_health rows). Reuses the always-up off-box surface rather than hitting
// radon-api, so the admin reliability sections keep reporting even when the
// trading stack is down. Never static-cached (cache contract).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// The daemon trust-splits /status: a request that carries proxy headers is an
// anonymous public-edge caller and gets the aggregate verdict ONLY. This route
// feeds the admin unit inventory and the WriterFreshnessTable rows, so it must
// read as a trusted caller or those sections silently render empty (and empty
// reads as green). Loopback first — on the VPS the daemon is a local hop with
// no proxy headers, which is exactly the trusted shape. The public edge stays
// as a fallback for laptop dev, where no local daemon is listening.
const LOOPBACK_STATUS_URL = "http://127.0.0.1:8330/status";
const PUBLIC_EDGE_STATUS_URL = "https://app.radon.run/edge-health/status";

const FETCH_TIMEOUT_MS = 5_000;

function statusSources(): string[] {
  const override = process.env.RADON_EDGE_HEALTH_URL;
  if (override) return [override];
  return [LOOPBACK_STATUS_URL, PUBLIC_EDGE_STATUS_URL];
}

// Server-side only (never NEXT_PUBLIC_): the shared bearer the daemon accepts
// in place of an unproxied on-box call. It keeps detail flowing on the edge
// fallback path without putting the token in a browser bundle.
function authHeaders(): Record<string, string> {
  const token = process.env.RADON_HEALTH_STATUS_TOKEN?.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function withApplicableRows(data: Record<string, unknown>): Record<string, unknown> {
  const serviceHealth = data.service_health;
  if (typeof serviceHealth !== "object" || serviceHealth === null) return data;
  const section = serviceHealth as Record<string, unknown>;
  if (!Array.isArray(section.rows)) return data;
  const rows = filterApplicableServiceHealthRows(section.rows);
  return { ...data, service_health: { ...section, rows, row_count: rows.length } };
}

async function readStatus(url: string): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
      headers: authHeaders(),
    });
    if (!res.ok) {
      // /status is always-200 by contract; a non-200 means the edge or daemon
      // is down. Report it as a payload flag, never a 4xx/5xx.
      throw new Error(`edge-health returned ${res.status}`);
    }
    return (await res.json()) as Record<string, unknown>;
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  let lastError = "edge-health fetch failed";
  for (const url of statusSources()) {
    try {
      const data = withApplicableRows(await readStatus(url));
      return setNoStoreResponseHeaders(NextResponse.json({ reachable: true, ...data }), requestId);
    } catch (error) {
      lastError = error instanceof Error ? error.message : "edge-health fetch failed";
    }
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({ reachable: false, error: lastError }),
    requestId,
  );
}
