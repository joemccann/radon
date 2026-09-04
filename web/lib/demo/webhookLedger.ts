// Clerk webhook replay-ledger claim, with one narrowly-scoped degrade.
//
// The ledger (demo_webhook_events) is the idempotency guard added by 4eaaf5e9.
// It is claimed BEFORE provisioning, so a claim that throws blocks provisioning
// entirely — which is exactly what happened when the table was missing from the
// demo Turso: every signup 500'd for 22 days and no trial was ever created.
//
// A missing idempotency STORE must not be able to deny service. That one error
// degrades to at-least-once provisioning (the provisioning steps are already
// idempotent); every other failure still throws so Clerk retries.

const MISSING_LEDGER = /no such table:\s*demo_webhook_events/i;

export function isMissingWebhookLedgerError(error: unknown): boolean {
  return error instanceof Error && MISSING_LEDGER.test(error.message);
}

export type WebhookClaim = {
  /** Whether to run provisioning for this delivery. */
  proceed: boolean;
  /** False when the delivery was provisioned without replay protection. */
  replayGuarded: boolean;
};

export async function claimWebhookEventOrDegrade(params: {
  eventId: string;
  eventType: string;
  claim: (eventId: string, eventType: string) => Promise<boolean>;
  onDegrade?: (reason: string) => void;
}): Promise<WebhookClaim> {
  const { eventId, eventType, claim, onDegrade } = params;
  try {
    const claimed = await claim(eventId, eventType);
    return { proceed: claimed, replayGuarded: true };
  } catch (error) {
    if (!isMissingWebhookLedgerError(error)) throw error;
    onDegrade?.("demo_webhook_events is missing — provisioning without replay protection");
    return { proceed: true, replayGuarded: false };
  }
}
