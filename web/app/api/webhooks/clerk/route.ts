// Clerk webhook — demo trial provisioning (demo.radon.run, Phase 2).
//
// Verifies the svix signature (Web Crypto, no svix dep), then on `user.created`
// provisions a 2-trading-day trial: writes Clerk publicMetadata + a demo_users
// row. Idempotent on retry. All business logic lives in lib/demo/* so this file
// is just transport.

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { verifySvixSignature } from "@/lib/demo/svixVerify";
import { computeTrialExpiry } from "@/lib/demo/trialExpiry";
import { getDemoDb } from "@/lib/db";
import {
  provisionDemoTrial,
  parseAllowedUserIds,
  type ClerkUserCreatedData,
} from "@/lib/demo/provisionTrial";
import type { DemoDbClient } from "@/lib/demo/demoUsers";
import type { DemoPublicMetadata } from "@/lib/demo/demoRole";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function setClerkMetadata(
  userId: string,
  publicMetadata: DemoPublicMetadata,
): Promise<void> {
  const { clerkClient } = await import("@clerk/nextjs/server");
  const client = await clerkClient();
  await client.users.updateUserMetadata(userId, { publicMetadata });
}

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const ok = (body: Record<string, unknown>, status = 200) =>
    setNoStoreResponseHeaders(NextResponse.json(body, { status }), requestId);

  const secret = process.env.CLERK_WEBHOOK_SECRET;
  if (!secret) {
    // Fail closed — an unconfigured secret means we cannot trust any payload.
    return ok({ error: "Webhook not configured.", requestId }, 500);
  }

  const body = await request.text();
  const verdict = await verifySvixSignature({
    secret,
    headers: {
      id: request.headers.get("svix-id"),
      timestamp: request.headers.get("svix-timestamp"),
      signature: request.headers.get("svix-signature"),
    },
    body,
  });
  if (!verdict.valid) {
    return ok({ error: "Invalid signature.", requestId }, 401);
  }

  let event: { type?: string; data?: ClerkUserCreatedData };
  try {
    event = JSON.parse(body);
  } catch {
    return ok({ error: "Malformed JSON.", requestId }, 400);
  }

  if (event.type !== "user.created" || !event.data) {
    // Acknowledge every other event so Clerk doesn't retry.
    return ok({ ignored: event.type ?? "unknown", requestId });
  }

  try {
    const result = await provisionDemoTrial(event.data, {
      db: getDemoDb() as unknown as DemoDbClient,
      computeExpiry: (startIsoEt) => computeTrialExpiry(startIsoEt),
      setClerkMetadata,
      allowedUserIds: parseAllowedUserIds(process.env.ALLOWED_USER_IDS),
      // On the demo deployment every non-operator signup is a trial — OAuth
      // signups through the sign-in page's social buttons never carry the
      // unsafe_metadata.demo marker the /sign-up page stamps.
      assumeDemo: process.env.NEXT_PUBLIC_RADON_DEMO === "1",
    });
    return ok({ ...result, requestId });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "provisioning failed";
    // 500 so Clerk retries (the provisioning steps are idempotent).
    return ok({ error: "Provisioning failed.", detail, requestId }, 500);
  }
}
