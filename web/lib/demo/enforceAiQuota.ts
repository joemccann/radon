// AI-quota enforcement for the 3 demo LLM routes (Phase 4).
//
// Single chokepoint the assistant / ticker-seasonality / ticker-info routes
// call BEFORE doing any expensive LLM work. Returns a `Response` to short out
// the handler (403 expired / 429 over quota / 401 no identity), or `null` to
// proceed.
//
// Default-safe for non-demo deploys: a request with no demoRole resolves to
// `null` IMMEDIATELY, before the demo DB is ever constructed — so prod
// (no TURSO_DEMO_DB_URL) is never affected and never throws.

import { NextResponse } from "next/server";
import { resolveDemoContext, type DemoPublicMetadata } from "./demoRole";
import {
  checkAndIncrementQuota,
  DEMO_AI_DAILY_LIMITS,
  type QuotaDbClient,
} from "./aiQuota";

export type DemoQuotaEndpoint = keyof typeof DEMO_AI_DAILY_LIMITS;

type AuthResult = {
  userId?: string | null;
  sessionClaims?: { metadata?: DemoPublicMetadata } | null;
  publicMetadata?: DemoPublicMetadata | null;
};

export type EnforceAiQuotaOptions = {
  // Injected for tests; default pulls Clerk's auth().
  authFn?: () => Promise<AuthResult>;
  // Injected for tests; default pulls the demo libsql client.
  dbFactory?: () => QuotaDbClient;
  now?: Date;
};

async function defaultAuth(): Promise<AuthResult> {
  const { auth } = await import("@clerk/nextjs/server");
  return (await auth()) as unknown as AuthResult;
}

async function defaultDb(): Promise<QuotaDbClient> {
  const { getDemoDb } = await import("@/lib/db");
  return getDemoDb() as unknown as QuotaDbClient;
}

/**
 * Enforce the per-user daily AI quota for `endpoint`.
 * @returns a `Response` that the route MUST return, or `null` to continue.
 */
export async function enforceDemoAiQuota(
  endpoint: DemoQuotaEndpoint,
  opts: EnforceAiQuotaOptions = {},
): Promise<Response | null> {
  const now = opts.now ?? new Date();
  const auth = await (opts.authFn ?? defaultAuth)();
  const metadata =
    auth?.sessionClaims?.metadata ?? auth?.publicMetadata ?? null;

  const ctx = resolveDemoContext(metadata, now.getTime());
  if (!ctx) return null; // not a demo user — no quota, no demo-DB access

  if (ctx.expired) {
    return NextResponse.json(
      { error: "Demo trial expired.", code: "DEMO_TRIAL_EXPIRED" },
      { status: 403 },
    );
  }

  const userId = auth?.userId ?? null;
  if (!userId) {
    return NextResponse.json(
      { error: "Unauthenticated.", code: "DEMO_NO_IDENTITY" },
      { status: 401 },
    );
  }

  const db = opts.dbFactory ? opts.dbFactory() : await defaultDb();
  const result = await checkAndIncrementQuota({ userId, endpoint, db, now });
  if (!result.allowed) {
    return NextResponse.json(
      {
        error: `Demo AI limit reached for ${endpoint} (${result.limit}/day). Resets ${result.resetAtEt}.`,
        code: "DEMO_AI_QUOTA_EXCEEDED",
        limit: result.limit,
        resetAtEt: result.resetAtEt,
      },
      { status: 429 },
    );
  }
  return null;
}
