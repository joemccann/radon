// Operator API for managing demo trials (demo.radon.run, Phase 6).
//
//   GET  → list every trial with today's AI burn + status.
//   POST → { action: "revoke" | "extend", userId, tradingDays? }
//
// Operator-gated (DEMO_ADMIN_USER_IDS) on the Next.js side — never just
// "any signed-in user". Reads/writes the SEPARATE demo Turso DB + Clerk
// metadata. Transport only; logic lives in lib/demo/*.

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDemoDb } from "@/lib/db";
import { requireDemoAdmin } from "@/lib/demo/adminAuth";
import {
  listDemoUsersWithUsage,
  revokeTrial,
  extendTrial,
  type AdminActionDeps,
} from "@/lib/demo/adminActions";
import { computeTrialExpiry } from "@/lib/demo/trialExpiry";
import type { DemoDbClient } from "@/lib/demo/demoUsers";
import type { DemoPublicMetadata } from "@/lib/demo/demoRole";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// This deployment has no demo backend (the prod app, not demo.radon.run) when
// TURSO_DEMO_DB_URL is unset. Return a clean 404 rather than letting getDemoDb
// throw a 500 — the operator panel's DemoUsersTable treats any non-200 as
// "no demo backend" and renders nothing.
function demoConfigured(): boolean {
  return Boolean(process.env.TURSO_DEMO_DB_URL);
}

async function setClerkMetadata(
  userId: string,
  meta: DemoPublicMetadata,
): Promise<void> {
  const { clerkClient } = await import("@clerk/nextjs/server");
  const client = await clerkClient();
  await client.users.updateUserMetadata(userId, { publicMetadata: meta });
}

function deps(): AdminActionDeps {
  return {
    db: getDemoDb() as unknown as DemoDbClient,
    setClerkMetadata,
    computeExpiry: (startIsoEt, tradingDays) =>
      computeTrialExpiry(startIsoEt, { tradingDays }),
  };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const admin = await requireDemoAdmin();
  if (!admin) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Forbidden", requestId }, { status: 403 }),
      requestId,
    );
  }
  if (!demoConfigured()) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "Demo backend not configured.", requestId }, { status: 404 }),
      requestId,
    );
  }
  const users = await listDemoUsersWithUsage(getDemoDb() as unknown as DemoDbClient);
  return setNoStoreResponseHeaders(
    NextResponse.json({ users, requestId }),
    requestId,
  );
}

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const reject = (message: string, status: number) =>
    setNoStoreResponseHeaders(
      NextResponse.json({ error: message, requestId }, { status }),
      requestId,
    );

  const admin = await requireDemoAdmin();
  if (!admin) return reject("Forbidden", 403);
  if (!demoConfigured()) return reject("Demo backend not configured.", 404);

  let body: { action?: string; userId?: string; tradingDays?: number };
  try {
    body = await request.json();
  } catch {
    return reject("Invalid JSON body", 400);
  }
  const { action, userId } = body;
  if (!userId) return reject("userId is required", 400);

  if (action === "revoke") {
    const result = await revokeTrial(userId, deps());
    if (!result.ok) return reject(result.reason, 404);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ok: true, action: "revoke", userId, requestId }),
      requestId,
    );
  }
  if (action === "extend") {
    const tradingDays = body.tradingDays ?? 3;
    const result = await extendTrial(userId, tradingDays, deps());
    if (!result.ok) return reject(result.reason, 400);
    return setNoStoreResponseHeaders(
      NextResponse.json(
        { ok: true, action: "extend", userId, expiresAt: result.expiresAt, requestId },
      ),
      requestId,
    );
  }
  return reject(`Unknown action: ${action}`, 400);
}
