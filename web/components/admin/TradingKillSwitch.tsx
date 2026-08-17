"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ConfirmDialog from "./ConfirmDialog";

const STATUS_POLL_MS = 5_000;
const KILL_CONFIRM_TOKEN = "KILL";

type HaltState = {
  halted: boolean;
  reason?: string | null;
};

type PendingAction = "halt" | "resume" | "cancel-all" | "kill" | null;

/**
 * Emergency trading controls (REL-029 / R-053): the browser affordance for
 * the REL-004 kill switch. Halt / Resume gate order placement; Cancel All
 * drains every working order; KILL does both (halt first, then mass-cancel).
 * Each destructive action sits behind a ConfirmDialog, and KILL additionally
 * requires typing the confirm token — the same guardrail class as the
 * order-risk chokepoint.
 */
export default function TradingKillSwitch() {
  const [status, setStatus] = useState<HaltState | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);
  const [confirmFor, setConfirmFor] = useState<Exclude<PendingAction, null> | null>(null);
  const inflightRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    if (inflightRef.current) return;
    inflightRef.current = true;
    try {
      const res = await fetch("/api/admin/trading/status", { cache: "no-store" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = (await res.json()) as HaltState;
      setStatus(data);
      setStatusError(null);
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "status probe failed");
    } finally {
      inflightRef.current = false;
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
    const id = window.setInterval(fetchStatus, STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [fetchStatus]);

  const runAction = useCallback(
    async (action: Exclude<PendingAction, null>) => {
      setPending(action);
      try {
        const needsConfirm = action === "kill" || action === "cancel-all";
        const res = await fetch(`/api/admin/trading/${action}`, {
          method: "POST",
          cache: "no-store",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(needsConfirm ? { confirm: true } : {}),
        });
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (!res.ok) {
          const detail =
            typeof (body.error as { message?: string })?.message === "string"
              ? (body.error as { message: string }).message
              : `HTTP ${res.status}`;
          setLastResult(`${action} failed: ${detail}`);
        } else {
          setLastResult(actionResultLine(action, body));
        }
      } catch (err) {
        setLastResult(
          `${action} failed: ${err instanceof Error ? err.message : "request error"}`,
        );
      } finally {
        setPending(null);
        setConfirmFor(null);
        void fetchStatus();
      }
    },
    [fetchStatus],
  );

  const halted = status?.halted === true;

  return (
    <section className="admin-card" data-testid="trading-kill-switch">
      <div className="admin-card-header">
        <h2 className="admin-card-title">Trading Controls</h2>
        <span
          className={`admin-pill ${halted ? "admin-pill-negative" : "admin-pill-positive"}`}
          data-testid="trading-halt-state"
        >
          {status === null ? "Unknown" : halted ? "HALTED" : "Active"}
        </span>
      </div>

      {halted && status?.reason && (
        <p className="admin-card-note" data-testid="trading-halt-reason">
          Halt reason: {status.reason}
        </p>
      )}
      {statusError && <p className="admin-card-note">Status unavailable: {statusError}</p>}

      <div className="admin-actions-row">
        {halted ? (
          <button
            type="button"
            className="admin-btn admin-btn-primary"
            onClick={() => setConfirmFor("resume")}
            disabled={pending !== null}
            title="Clear the trading halt so new orders can be placed"
            data-testid="trading-resume-button"
          >
            Resume Trading
          </button>
        ) : (
          <button
            type="button"
            className="admin-btn admin-btn-ghost"
            onClick={() => setConfirmFor("halt")}
            disabled={pending !== null}
            title="Block all new order placement (working orders stay live)"
            data-testid="trading-halt-button"
          >
            Halt Trading
          </button>
        )}

        <button
          type="button"
          className="admin-btn admin-btn-ghost"
          onClick={() => setConfirmFor("cancel-all")}
          disabled={pending !== null}
          title="Cancel EVERY working order (master global cancel + drain verify)"
          data-testid="trading-cancel-all-button"
        >
          Cancel All Orders
        </button>

        <button
          type="button"
          className="admin-btn admin-btn-danger"
          onClick={() => setConfirmFor("kill")}
          disabled={pending !== null}
          title="Kill switch: halt new placements first, then cancel every working order"
          data-testid="trading-kill-button"
        >
          {pending === "kill" ? "Killing..." : "Kill Switch"}
        </button>
      </div>

      {lastResult && (
        <p className="admin-card-note" data-testid="trading-last-result">
          {lastResult}
        </p>
      )}

      <ConfirmDialog
        open={confirmFor === "halt"}
        title="Halt trading?"
        body="Blocks all NEW order placement across every surface until resumed. Working orders stay live; use Cancel All or the Kill Switch to drain them."
        confirmLabel="Halt"
        destructive
        pending={pending === "halt"}
        onConfirm={() => void runAction("halt")}
        onCancel={() => setConfirmFor(null)}
      />
      <ConfirmDialog
        open={confirmFor === "resume"}
        title="Resume trading?"
        body="Clears the trading halt. New order placement is allowed again immediately."
        confirmLabel="Resume"
        pending={pending === "resume"}
        onConfirm={() => void runAction("resume")}
        onCancel={() => setConfirmFor(null)}
      />
      <ConfirmDialog
        open={confirmFor === "cancel-all"}
        title="Cancel ALL working orders?"
        body="Fires the master global cancel: EVERY working order on the account is cancelled, including exit orders. This does not halt new placements."
        confirmLabel="Cancel All"
        destructive
        pending={pending === "cancel-all"}
        onConfirm={() => void runAction("cancel-all")}
        onCancel={() => setConfirmFor(null)}
      />
      <ConfirmDialog
        open={confirmFor === "kill"}
        title="Fire the kill switch?"
        body="Halts all new order placement FIRST, then cancels EVERY working order (including exit orders). Use when something is placing orders that should not be. Resume Trading re-arms placement afterwards."
        confirmLabel="Kill"
        destructive
        requireTyped={KILL_CONFIRM_TOKEN}
        pending={pending === "kill"}
        onConfirm={() => void runAction("kill")}
        onCancel={() => setConfirmFor(null)}
      />
    </section>
  );
}

function actionResultLine(
  action: Exclude<PendingAction, null>,
  body: Record<string, unknown>,
): string {
  if (action === "kill") {
    const cancel = body.cancel as { cancelled?: number } | undefined;
    const cancelled = typeof cancel?.cancelled === "number" ? cancel.cancelled : null;
    return cancelled === null
      ? "kill switch fired: halted + cancel sweep run"
      : `kill switch fired: halted, ${cancelled} order(s) cancelled`;
  }
  if (action === "cancel-all") {
    const cancelled = typeof body.cancelled === "number" ? body.cancelled : null;
    return cancelled === null
      ? "cancel-all run"
      : `cancel-all run: ${cancelled} order(s) cancelled`;
  }
  if (action === "halt") return "trading halted";
  return "trading resumed";
}
