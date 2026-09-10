import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { cachedReadResult } from "@/lib/dbCache";
import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "internal";

const FLASH_URL = "https://api.mktnews.net/api/flash?lang=en";
const FLASH_ORIGIN = "https://mktnews.net";
const FLASH_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const SNAPSHOT_CACHE_TTL_MS = 30_000;
const SNAPSHOT_MAX_STALE_MS = 5 * 60_000;
const RING_SIZE = 50;
const MAX_CONTENT_CHARS = 2_000;
const MAX_IMPACT = 8;

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function clip(value: unknown, max: number): string {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, max);
}

function normalizeImpact(value: unknown): Array<{ symbol: string; impact: string }> {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_IMPACT).flatMap((candidate) => {
    const row = asRecord(candidate);
    if (!row) return [];
    const symbol = clip(row.symbol, 16);
    const impact = clip(row.impact, 16);
    return symbol || impact ? [{ symbol, impact }] : [];
  });
}

function normalizeHeadline(value: unknown) {
  const row = asRecord(value);
  const body = asRecord(row?.data);
  if (!row || !body) return null;
  const id = clip(row.id, 80);
  const content = clip(body.content || body.title, MAX_CONTENT_CHARS);
  if (!id || !content) return null;
  return {
    kind: "headline" as const,
    id,
    time: typeof row.time === "string" ? row.time : null,
    important: Boolean(row.important),
    content,
    impact: normalizeImpact(row.impact),
  };
}

export function normalizeFlashSnapshot(payload: unknown) {
  const root = asRecord(payload);
  const rows = Array.isArray(root?.data) ? root.data : Array.isArray(payload) ? payload : null;
  if (!rows) throw new Error("Invalid headline provider response");
  const items = rows
    .slice(0, RING_SIZE)
    .reverse()
    .map(normalizeHeadline)
    .filter((row): row is NonNullable<typeof row> => row !== null);
  if (rows.length > 0 && items.length === 0) {
    throw new Error("Headline provider rows did not match the expected schema");
  }
  return items;
}

async function fetchSnapshot() {
  try {
    const response = await fetch(FLASH_URL, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Origin: FLASH_ORIGIN,
        "User-Agent": FLASH_USER_AGENT,
      },
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error(`Headline provider returned ${response.status}`);
    const items = normalizeFlashSnapshot(await response.json());
    // REL-246 (R-655): the flash feed carries no signal distinguishing a quiet
    // tape from a blocked provider, so an empty payload is a provider failure —
    // never cached as a healthy snapshot.
    if (items.length === 0) throw new Error("Headline provider returned an empty snapshot");
    return items;
  } catch (error) {
    // REL-246 (R-655): provider failures were silent; log so the watchdog
    // sees the outage even when a stale cache absorbs it.
    console.error("[headlines] provider snapshot failed", {
      message: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}

export async function GET() {
  const requestId = getRequestId();
  if (process.env.NEXT_PUBLIC_RADON_DEMO !== "1") {
    return setNoStoreResponseHeaders(jsonApiError({
      message: "Not found",
      status: 404,
      code: "NOT_FOUND",
      requestId,
    }), requestId);
  }

  const access = await requireRouteAccess();
  if (!access.ok) return access.response;

  try {
    const snapshot = await cachedReadResult("demo:headlines", SNAPSHOT_CACHE_TTL_MS, fetchSnapshot, {
      staleWhileError: true,
      maxStaleMs: SNAPSHOT_MAX_STALE_MS,
    });
    return setNoStoreResponseHeaders(NextResponse.json({
      items: snapshot.value,
      degraded: snapshot.staleWhileError,
    }), requestId);
  } catch {
    return setNoStoreResponseHeaders(jsonApiError({
      message: "Headlines temporarily unavailable",
      status: 503,
      code: "UPSTREAM_ERROR",
      requestId,
    }), requestId);
  }
}
