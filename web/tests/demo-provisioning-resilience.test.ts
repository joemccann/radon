/**
 * 2026-09-03 demo outage regression suite.
 *
 * Commit 4eaaf5e9 added a replay ledger whose table was never created in the
 * demo Turso, so every Clerk user.created webhook threw before provisioning —
 * and the same commit's default-deny turned that into a bare 403 on every
 * page. Nothing alerted for 22 days. These tests pin all three halves.
 */
import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { NextRequest } from "next/server";
import { handleDemoGate } from "@/lib/demo/demoGate";
import {
  isMissingWebhookLedgerError,
  claimWebhookEventOrDegrade,
} from "@/lib/demo/webhookLedger";
import { isPublicRoute } from "@/middleware";

const NOW = Date.parse("2026-09-03T12:00:00-04:00");
// Resolve against THIS file, never process.cwd() — the workspace runner
// invokes the suite from web/ or site/ depending on where vitest was started.
const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = (rel: string) => readFileSync(path.join(WEB_ROOT, rel), "utf8");

describe("webhook replay ledger degrades only on a missing table", () => {
  it("classifies the exact SQLite missing-table error and nothing else", () => {
    expect(
      isMissingWebhookLedgerError(
        new Error("SQLITE_UNKNOWN: SQLite error: no such table: demo_webhook_events"),
      ),
    ).toBe(true);
    expect(isMissingWebhookLedgerError(new Error("no such table: demo_users"))).toBe(false);
    expect(isMissingWebhookLedgerError(new Error("UNIQUE constraint failed"))).toBe(false);
    expect(isMissingWebhookLedgerError(new Error("network timeout"))).toBe(false);
    expect(isMissingWebhookLedgerError("no such table: demo_webhook_events")).toBe(false);
  });

  it("claims normally when the ledger exists", async () => {
    const claim = vi.fn().mockResolvedValue(true);
    const res = await claimWebhookEventOrDegrade({
      eventId: "evt_1",
      eventType: "user.created",
      claim,
    });
    expect(res).toEqual({ proceed: true, replayGuarded: true });
    expect(claim).toHaveBeenCalledOnce();
  });

  it("stops on a duplicate delivery", async () => {
    const res = await claimWebhookEventOrDegrade({
      eventId: "evt_1",
      eventType: "user.created",
      claim: async () => false,
    });
    expect(res).toEqual({ proceed: false, replayGuarded: true });
  });

  it("provisions unguarded rather than blocking when the ledger table is absent", async () => {
    const onDegrade = vi.fn();
    const res = await claimWebhookEventOrDegrade({
      eventId: "evt_1",
      eventType: "user.created",
      claim: async () => {
        throw new Error("SQLITE_UNKNOWN: SQLite error: no such table: demo_webhook_events");
      },
      onDegrade,
    });
    expect(res).toEqual({ proceed: true, replayGuarded: false });
    expect(onDegrade).toHaveBeenCalledOnce();
  });

  it("rethrows every other claim failure so Clerk retries", async () => {
    await expect(
      claimWebhookEventOrDegrade({
        eventId: "evt_1",
        eventType: "user.created",
        claim: async () => {
          throw new Error("network timeout");
        },
      }),
    ).rejects.toThrow("network timeout");
  });

  it("the route goes through the degrading claim, not the raw one", () => {
    const route = source("app/api/webhooks/clerk/route.ts");
    expect(route).toContain("claimWebhookEventOrDegrade");
    // Replay protection stays claim-FIRST: provisioning must not run before it.
    expect(route.indexOf("claimWebhookEventOrDegrade")).toBeLessThan(
      route.indexOf("provisionDemoTrial("),
    );
    expect(route).toContain("notifyDemoProvisioningFailure");
  });
});

describe("unprovisioned demo users get a pending surface, not a bare 403", () => {
  it("redirects a page request to /demo-pending", async () => {
    const res = await handleDemoGate(
      {
        userId: "pending",
        metadata: null,
        request: new NextRequest("https://demo.radon.run/portfolio"),
      },
      { now: NOW, demoDeployment: true },
    );
    expect(res?.status).toBe(307);
    expect(new URL(res!.headers.get("location")!).pathname).toBe("/demo-pending");
  });

  it("keeps the hard 403 JSON on API paths", async () => {
    const res = await handleDemoGate(
      {
        userId: "pending",
        metadata: null,
        request: new NextRequest("https://demo.radon.run/api/portfolio"),
      },
      { now: NOW, demoDeployment: true },
    );
    expect(res?.status).toBe(403);
    expect((await res!.json()).code).toBe("DEMO_ACCESS_PENDING");
  });

  it("never engages off the demo deployment", async () => {
    const res = await handleDemoGate(
      {
        userId: "operator",
        metadata: null,
        request: new NextRequest("https://app.radon.run/portfolio"),
      },
      { now: NOW },
    );
    expect(res).toBeNull();
  });
});

describe("/demo-pending perimeter", () => {
  const req = (url: string) => new NextRequest(url);

  it("is public so the redirect cannot loop on itself", () => {
    expect(isPublicRoute(req("https://demo.radon.run/demo-pending"))).toBe(true);
  });

  it("does not widen the perimeter to the workspace root", () => {
    expect(isPublicRoute(req("https://app.radon.run/"))).toBe(false);
    expect(isPublicRoute(req("https://app.radon.run/portfolio"))).toBe(false);
    expect(isPublicRoute(req("https://app.radon.run/api/portfolio"))).toBe(false);
  });

  it("renders no shell, fetches no account data, echoes no identity", () => {
    const page = source("app/demo-pending/page.tsx");
    expect(page).not.toContain("WorkspaceShell");
    expect(page).not.toContain("fetch(");
    expect(page).not.toContain("currentUser");
    expect(page).not.toContain("auth(");
  });

  it("bounds its own retry loop", () => {
    const client = source("app/demo-pending/DemoPendingRetry.tsx");
    expect(client).toContain("MAX_ATTEMPTS");
    expect(client).toMatch(/attempts?\s*(>=|>)\s*MAX_ATTEMPTS|MAX_ATTEMPTS\s*(<=|<)/);
    expect(client).not.toContain("email");
  });
});

describe("provisioning failures page the operator, without PII", () => {
  it("sends counts and a reason only — never a user id or email", () => {
    const notify = source("lib/notify/pushover.ts");
    expect(notify).toContain("PUSHOVER_TOKEN");
    expect(notify).toContain("PUSHOVER_USER");
    // No-ops rather than throwing when unconfigured: alerting must never be
    // able to fail a webhook that would otherwise have provisioned a trial.
    expect(notify).toMatch(/if \(!token \|\| !user\)/);
    const route = source("app/api/webhooks/clerk/route.ts");
    expect(route).not.toMatch(/notifyDemoProvisioningFailure\([^)]*email/);
    expect(route).not.toMatch(/notifyDemoProvisioningFailure\([^)]*userId/);
  });
});
