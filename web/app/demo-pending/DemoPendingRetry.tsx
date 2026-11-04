"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { resolveTrialState, type DemoPublicMetadata } from "@/lib/demo/demoContext";

// A freshly created demo user's trial reaches the session JWT only on token
// refresh, so the gate can bounce them here for up to a refresh interval even
// when provisioning worked. Force a fresh token on a BOUNDED schedule and send
// them on as soon as the claim arrives: only a token whose trial claim has
// left `pending` navigates, so a still-pending visitor is not bounced through
// the gate on every tick.
const MAX_ATTEMPTS = 12;
const INTERVAL_MS = 5_000;

/** Read (not verify) the session token's demo claim; unreadable = pending. */
function claimResolved(token: string | null): boolean {
  const body = token?.split(".")[1];
  if (!body) return false;
  try {
    const json = atob(body.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as { metadata?: DemoPublicMetadata };
    const state = resolveTrialState(claims.metadata);
    return state !== null && state.kind !== "pending";
  } catch {
    return false;
  }
}

export default function DemoPendingRetry() {
  const { getToken, isLoaded } = useAuth();
  const router = useRouter();
  const [attempts, setAttempts] = useState(0);

  useEffect(() => {
    if (!isLoaded) return;
    if (attempts >= MAX_ATTEMPTS) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      let token: string | null = null;
      try {
        token = await getToken({ skipCache: true });
      } catch {
        // A refresh failure is indistinguishable from "not ready"; just retry.
      }
      if (cancelled) return;
      if (claimResolved(token)) {
        router.refresh();
        router.replace("/portfolio");
        return;
      }
      setAttempts((n) => n + 1);
    }, INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [attempts, getToken, isLoaded, router]);

  if (attempts >= MAX_ATTEMPTS) {
    return (
      <p className="trial-expired-copy">
        This is taking longer than expected. Try signing out and back in, or get
        in touch at{" "}
        <a href="https://radon.run" className="trial-expired-link">
          radon.run
        </a>
        .
      </p>
    );
  }

  return (
    <p className="trial-expired-copy" aria-live="polite">
      Checking access… ({attempts + 1}/{MAX_ATTEMPTS})
    </p>
  );
}
