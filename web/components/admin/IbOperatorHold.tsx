"use client";

import { useCallback, useState } from "react";
import type { OperatorHold } from "@/lib/adminTypes";
import ErrorToast from "@/components/ErrorToast";
import { userErrorMessage } from "@/lib/userError";
import ConfirmDialog from "./ConfirmDialog";

const HOLD_URL = "/api/admin/ib/operator-hold";
const HOLD_CONFIRM_TOKEN = "HOLD";

type Props = {
  /** Mirrored from /health ib_gateway.operator_hold; null/undefined = unknown. */
  hold: OperatorHold | null | undefined;
  /** Shared workspace lock or stale observation; disables both actions. */
  disabledReason?: string | null;
  onAfter?: () => void;
};

/**
 * IBKR operator hold. The Gateway and the operator share one IBKR username,
 * and IBKR allows one session per username. Hold stops the Gateway on the
 * broker and keeps every recovery path (watchdog, relay, admin restart) from
 * logging it back in, so the operator can flatten from IBKR Mobile or the web
 * portal. Resume clears it and logs the Gateway in once (one 2FA push).
 */
export default function IbOperatorHold({ hold, disabledReason = null, onAfter }: Props) {
  const [reason, setReason] = useState("");
  const [confirmFor, setConfirmFor] = useState<"hold" | "resume" | null>(null);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const known = hold != null;
  const held = hold?.held === true;
  const trimmedReason = reason.trim();
  const holdDisabledReason = disabledReason ??
    (!known ? "Hold state is unknown while the broker is unreachable. On the broker run radon ib release." : null) ??
    (trimmedReason ? null : "Enter a reason to hold the Gateway.");
  const resumeDisabledReason = disabledReason ??
    (!known ? "Hold state is unknown while the broker is unreachable. On the broker run radon ib resume." : null);

  const submit = useCallback(async (nextHeld: boolean) => {
    // Re-check the gate at the moment of sending, not at render time.
    if (pending) return;
    if (nextHeld && (holdDisabledReason || !trimmedReason)) return;
    if (!nextHeld && resumeDisabledReason) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch(HOLD_URL, {
        method: "POST",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(nextHeld ? { held: true, reason: trimmedReason } : { held: false }),
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        const message = (body.error as { message?: string } | undefined)?.message ?? `HTTP ${res.status}`;
        setError(userErrorMessage(message, "The broker did not confirm. On the broker run radon ib status."));
      } else {
        setResult(typeof body.detail === "string" ? body.detail : nextHeld ? "Hold set." : "Hold cleared.");
        if (nextHeld) setReason("");
      }
    } catch (err) {
      setError(userErrorMessage(err, "request error"));
    } finally {
      setPending(false);
      setConfirmFor(null);
      onAfter?.();
    }
  }, [pending, holdDisabledReason, resumeDisabledReason, trimmedReason, onAfter]);

  return (
    <section className="admin-card" data-testid="ib-operator-hold">
      <header className="admin-card-header">
        <span className="admin-card-title">IBKR operator hold</span>
        <span
          className={`admin-pill ${!known ? "admin-pill-neutral" : held ? "admin-pill-negative" : "admin-pill-positive"}`}
          data-testid="ib-operator-hold-state"
        >
          {!known ? "Unknown" : held ? "HELD" : "Not held"}
        </span>
      </header>

      {held ? (
        <p className="admin-card-note" data-testid="ib-operator-hold-detail">
          Gateway held by {hold?.actor ?? "unknown"} since {hold?.held_at ?? "unknown"}: {hold?.reason ?? "no reason"}.
          {hold?.expired ? ` Expiry ${hold.expires_at} has passed; still held until you resume.` : ""}
          {" "}Nothing logs the Gateway back in until you resume.
        </p>
      ) : (
        <p className="admin-card-note">
          Hold before you log in to IBKR Mobile or the web portal. The Gateway stops and stays logged out so it does not take the session back.
        </p>
      )}

      <div className="admin-actions-row">
        {held ? (
          <button
            type="button"
            className="admin-btn admin-btn-primary"
            onClick={() => setConfirmFor("resume")}
            disabled={pending || resumeDisabledReason !== null}
            title={resumeDisabledReason ?? "Clear the hold and log the Gateway in once"}
            data-testid="ib-operator-hold-resume"
          >
            {pending ? "Working..." : "Resume Gateway"}
          </button>
        ) : (
          <>
            <input
              type="text"
              className="admin-confirm-typed-input"
              placeholder="Reason (e.g. flatten on IBKR Mobile)"
              value={reason}
              maxLength={200}
              onChange={(e) => setReason(e.target.value)}
              aria-label="Hold reason"
              data-testid="ib-operator-hold-reason"
            />
            <button
              type="button"
              className="admin-btn admin-btn-danger"
              onClick={() => setConfirmFor("hold")}
              disabled={pending || holdDisabledReason !== null}
              title={holdDisabledReason ?? "Stop the Gateway and keep it logged out"}
              data-testid="ib-operator-hold-set"
            >
              {pending ? "Working..." : "Hold Gateway"}
            </button>
          </>
        )}
      </div>

      {error && <ErrorToast message={error} testId="ib-operator-hold-error" />}
      {result && !error && <p className="admin-card-note" data-testid="ib-operator-hold-result">{result}</p>}

      <ConfirmDialog
        open={confirmFor === "hold"}
        title="Hold the IB Gateway?"
        body="The broker stops the Gateway and keeps it logged out. Watchdog, relay and admin restarts all stand down. IB data and Radon orders are offline until you resume. Wait about 30 seconds, then log in to IBKR."
        confirmLabel="Hold Gateway"
        destructive
        requireTyped={HOLD_CONFIRM_TOKEN}
        pending={pending}
        confirmDisabled={pending || holdDisabledReason !== null}
        disabledReason={holdDisabledReason ?? undefined}
        onConfirm={() => void submit(true)}
        onCancel={() => setConfirmFor(null)}
      />
      <ConfirmDialog
        open={confirmFor === "resume"}
        title="Resume the IB Gateway?"
        body="Log out of IBKR Mobile and the web portal first: the Gateway takes the session back. The hold clears and the Gateway logs in once. Approve the one IBKR Mobile push."
        confirmLabel="Resume Gateway"
        pending={pending}
        confirmDisabled={pending || resumeDisabledReason !== null}
        disabledReason={resumeDisabledReason ?? undefined}
        onConfirm={() => void submit(false)}
        onCancel={() => setConfirmFor(null)}
      />
    </section>
  );
}
