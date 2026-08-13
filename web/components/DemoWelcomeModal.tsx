"use client";

import { useEffect, useState } from "react";
import { useUser } from "@clerk/nextjs";
import Modal from "@/components/Modal";
// demoContext, NOT demoRole — demoRole pulls @clerk/nextjs/server (server-only)
// which Turbopack refuses inside a Client Component graph.
import { resolveDemoContext, type DemoPublicMetadata } from "@/lib/demo/demoContext";

const SEEN_KEY_PREFIX = "radon:demo-welcome:";

export function formatExpiryEt(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "---";
  const formatted = parsed.toLocaleString("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `${formatted} ET`;
}

/**
 * Demo trial welcome — shown once per browser session after a demo trial user
 * signs in, stating the trial terms and the exact expiry timestamp from Clerk
 * publicMetadata (written by the user.created webhook). Renders nothing for
 * non-demo deployments, non-trial users, and expired trials (the middleware
 * gate owns the expired path).
 */
export default function DemoWelcomeModal() {
  return <ClerkDemoWelcomeModal />;
}

function ClerkDemoWelcomeModal() {
  const { user, isLoaded } = useUser();
  const [open, setOpen] = useState(false);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_RADON_DEMO !== "1") return;
    if (!isLoaded || !user) return;
    const ctx = resolveDemoContext(
      user.publicMetadata as DemoPublicMetadata | undefined,
    );
    if (!ctx || ctx.expired || !ctx.trialExpiresAt) return;
    const seenKey = SEEN_KEY_PREFIX + user.id;
    try {
      if (sessionStorage.getItem(seenKey)) return;
      sessionStorage.setItem(seenKey, "1");
    } catch {
      return; // storage unavailable — skip rather than re-open on every render
    }
    setExpiresAt(ctx.trialExpiresAt);
    setOpen(true);
  }, [isLoaded, user]);

  if (!open || !expiresAt) return null;

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      title="Demo access"
      className="demo-welcome-modal"
    >
      <p className="demo-welcome-lead">
        Welcome to the Radon demo. Your trial includes 2 trading days of full
        access to the terminal with sample market data.
      </p>
      <div className="demo-welcome-expiry">
        <span className="demo-welcome-expiry-label">Access expires</span>
        <span className="demo-welcome-expiry-value" data-testid="demo-expiry">
          {formatExpiryEt(expiresAt)}
        </span>
      </div>
      <p className="demo-welcome-note">
        After that, this account is closed and you will be redirected to
        radon.run.
      </p>
      <button
        className="demo-welcome-cta"
        onClick={() => setOpen(false)}
        autoFocus
      >
        Start exploring
      </button>
    </Modal>
  );
}
