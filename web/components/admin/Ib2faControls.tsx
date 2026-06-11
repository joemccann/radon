"use client";

import { useEffect, useState } from "react";
import type { AdminHealthPayload, UnitStatus } from "@/lib/adminTypes";
import {
  forcePushDisabledReason,
  gatewayPowerState,
  isForcePushDisabled,
  unitDependents,
  type GatewayPowerState,
} from "@/lib/adminFormat";
import ConfirmDialog from "./ConfirmDialog";

const GATEWAY_UNIT = "radon-ib-gateway.service";
// After a confirmed Stop/Start the unit-state poll lags by a beat, so we flip
// the button optimistically. This is the safety ceiling: the override always
// clears after this window even if the poll never settles, so it can't stick.
const OPTIMISTIC_POWER_MAX_MS = 120_000;

type Ib2faControlsProps = {
  health: AdminHealthPayload | null;
  onForcePush: () => Promise<void>;
  onResetBackoff: () => Promise<void>;
  onRestartStack: () => Promise<void>;
  /** The radon-ib-gateway.service unit row (filtered from the table, still
   *  surfaced here for the power control's active_state). */
  gatewayUnit?: UnitStatus | null;
  /** Whether the host supports systemctl control (false off-VPS). */
  servicesSupported?: boolean;
  /** Targeted per-unit stop of the gateway (control_unit / systemctl stop). */
  onStopGateway?: () => Promise<void>;
  /** Full-stack recovery (radon restart): brings the gateway + dependents back
   *  in order. Reuses the Restart All Services path. */
  onStartGateway?: () => Promise<void>;
  onAfter?: () => void;
};

/**
 * Two-button control surface: Force 2FA Push (primary) + Reset Backoff
 * (secondary). Each button gates on its own confirmation modal. Force push
 * is also gated on the cross-process lock surfaced by /health.
 */
export default function Ib2faControls({
  health,
  onForcePush,
  onResetBackoff,
  onRestartStack,
  gatewayUnit = null,
  servicesSupported = true,
  onStopGateway,
  onStartGateway,
  onAfter,
}: Ib2faControlsProps) {
  const [showForceConfirm, setShowForceConfirm] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [showStartConfirm, setShowStartConfirm] = useState(false);
  const [pendingForce, setPendingForce] = useState(false);
  const [pendingReset, setPendingReset] = useState(false);
  const [pendingRestart, setPendingRestart] = useState(false);
  const [pendingPower, setPendingPower] = useState(false);
  // Optimistic power target set on a confirmed Stop/Start so the button flips
  // immediately rather than waiting for the next /admin/services poll. Cleared
  // once the poll settles to the expected terminal state (or after the safety
  // window) so it never sticks stale.
  const [optimisticPower, setOptimisticPower] = useState<GatewayPowerState | null>(null);

  const pushLock = health?.ib_gateway?.restart_backoff?.push_lock ?? null;
  const disableForce = isForcePushDisabled({ pushLock, pending: pendingForce });
  const disableReason = forcePushDisabledReason({ pushLock, pending: pendingForce });

  const polledPowerState = gatewayPowerState({
    unit: gatewayUnit,
    portListening: health?.ib_gateway?.port_listening,
  });
  const powerState = optimisticPower ?? polledPowerState;

  // Reconcile the optimistic override with the authoritative poll: clear it
  // once the poll confirms the expected terminal state, or after the safety
  // window so a missed/failed action can never leave the button wrong forever.
  useEffect(() => {
    if (optimisticPower === null) return undefined;
    const settled =
      (optimisticPower === "stopped" && polledPowerState === "stopped") ||
      ((optimisticPower === "running" || optimisticPower === "transitional") &&
        polledPowerState === "running");
    if (settled) {
      setOptimisticPower(null);
      return undefined;
    }
    const timer = setTimeout(() => setOptimisticPower(null), OPTIMISTIC_POWER_MAX_MS);
    return () => clearTimeout(timer);
  }, [optimisticPower, polledPowerState]);

  const gatewayDependents = unitDependents(GATEWAY_UNIT);
  // Start triggers a fresh 2FA login, so gate it on the same push lock as
  // Force 2FA to keep two pushes from racing (feedback_2fa_push_stacking).
  const startBlockedByPush = isForcePushDisabled({ pushLock, pending: false });
  const powerDisabledReason = !servicesSupported
    ? "Read-only: this browser is not on the Hetzner VPS."
    : powerState === "transitional"
      ? "Gateway is mid-transition. Wait for it to settle."
      : powerState === "stopped" && startBlockedByPush
        ? (forcePushDisabledReason({ pushLock, pending: false }) ?? "A 2FA push is already in flight.")
        : null;
  const powerDisabled = pendingPower || powerDisabledReason !== null;

  const runForce = async () => {
    setPendingForce(true);
    try {
      await onForcePush();
    } finally {
      setPendingForce(false);
      setShowForceConfirm(false);
      onAfter?.();
    }
  };

  const runReset = async () => {
    setPendingReset(true);
    try {
      await onResetBackoff();
    } finally {
      setPendingReset(false);
      setShowResetConfirm(false);
      onAfter?.();
    }
  };

  const runRestart = async () => {
    setPendingRestart(true);
    try {
      await onRestartStack();
    } finally {
      setPendingRestart(false);
      setShowRestartConfirm(false);
      onAfter?.();
    }
  };

  const runStop = async () => {
    setPendingPower(true);
    try {
      await onStopGateway?.();
      // Flip to "Start Gateway" immediately; the poll settles a beat later.
      setOptimisticPower("stopped");
    } finally {
      setPendingPower(false);
      setShowStopConfirm(false);
      onAfter?.();
    }
  };

  const runStart = async () => {
    setPendingPower(true);
    try {
      await onStartGateway?.();
      // Start is a full-stack restart (~60-90s); show it as in-transition
      // until the poll confirms the gateway is back up.
      setOptimisticPower("transitional");
    } finally {
      setPendingPower(false);
      setShowStartConfirm(false);
      onAfter?.();
    }
  };

  const powerStatusLine =
    powerState === "running"
      ? "Gateway is running. IB data plane live."
      : powerState === "transitional"
        ? "Gateway is mid-transition."
        : "Gateway is stopped. IB, orders relay, and monitor are offline.";

  const powerButtonLabel = pendingPower
    ? "Working..."
    : powerState === "transitional"
      ? gatewayUnit?.active_state === "deactivating"
        ? "Stopping..."
        : "Starting..."
      : powerState === "running"
        ? "Stop Gateway"
        : "Start Gateway";

  return (
    <section className="admin-card" data-testid="ib-controls">
      <header className="admin-card-header">
        <span className="admin-card-title">IB Gateway controls</span>
      </header>

      <div className="admin-actions-row">
        <button
          type="button"
          className="admin-btn admin-btn-primary"
          onClick={() => setShowForceConfirm(true)}
          disabled={disableForce}
          title={disableReason ?? "Fires a fresh IBKR Mobile 2FA push"}
          data-testid="force-2fa-button"
        >
          Force 2FA Push
        </button>

        <button
          type="button"
          className="admin-btn admin-btn-ghost"
          onClick={() => setShowResetConfirm(true)}
          disabled={pendingReset}
          title="Release the push lock and clear the restart backoff counter"
          data-testid="reset-backoff-button"
        >
          Reset Backoff
        </button>

        <button
          type="button"
          className="admin-btn admin-btn-danger"
          onClick={() => setShowRestartConfirm(true)}
          disabled={pendingRestart}
          title="Run radon restart on the VPS: stops then starts every radon-* unit in order"
          data-testid="restart-stack-button"
        >
          {pendingRestart ? "Restarting..." : "Restart All Services"}
        </button>
      </div>

      {disableReason && (
        <p className="admin-card-note" data-testid="force-2fa-disabled-reason">
          {disableReason}
        </p>
      )}

      <div className="admin-gateway-power" data-testid="gateway-power">
        <span className="admin-card-note-inline">Gateway power</span>
        <p
          className="admin-gateway-power-status"
          data-testid="gateway-power-status"
          data-state={powerState}
        >
          {powerStatusLine}
        </p>
        <button
          type="button"
          className={`admin-btn admin-gateway-power-btn ${powerState === "running" ? "admin-btn-danger" : "admin-btn-primary"}`}
          onClick={() =>
            powerState === "running"
              ? setShowStopConfirm(true)
              : setShowStartConfirm(true)
          }
          disabled={powerDisabled}
          title={powerDisabledReason ?? undefined}
          data-testid="gateway-power-button"
        >
          {powerButtonLabel}
        </button>
        {powerDisabledReason && (
          <p className="admin-card-note" data-testid="gateway-power-disabled-reason">
            {powerDisabledReason}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={showForceConfirm}
        title="Force 2FA push?"
        body="This fires an IBKR Mobile 2FA push to your phone. Approve quickly to avoid stacking another push and confusing the IBKR backend."
        confirmLabel="Send push"
        destructive
        pending={pendingForce}
        onConfirm={runForce}
        onCancel={() => setShowForceConfirm(false)}
      />
      <ConfirmDialog
        open={showResetConfirm}
        title="Reset restart backoff?"
        body="Use only after manually approving the in-flight 2FA push outside the lock window. This clears the backoff counter and releases the push lock so the next legitimate restart fires immediately."
        confirmLabel="Reset"
        pending={pendingReset}
        onConfirm={runReset}
        onCancel={() => setShowResetConfirm(false)}
      />
      <ConfirmDialog
        open={showRestartConfirm}
        title="Restart all radon services?"
        body="Runs radon restart on the VPS: stops every radon-* systemd unit, then starts them in dependency order (IB Gateway first). Takes about 60 to 90 seconds. The page will briefly lose its connection while FastAPI cycles. IB Gateway will need a fresh 2FA approval on your phone when it comes back up."
        confirmLabel="Restart all"
        destructive
        pending={pendingRestart}
        onConfirm={runRestart}
        onCancel={() => setShowRestartConfirm(false)}
      />
      <ConfirmDialog
        open={showStopConfirm}
        title="Stop the IB Gateway?"
        body="This runs systemctl stop on the IB Gateway. It takes IB offline and cascade-stops the API, realtime relay, and monitor. Those will NOT come back on their own. To bring everything back, use Start Gateway (or Restart All Services), which restarts the whole stack in order."
        confirmLabel="Stop Gateway"
        destructive
        affectedUnits={gatewayDependents}
        requireTyped={GATEWAY_UNIT}
        pending={pendingPower}
        onConfirm={runStop}
        onCancel={() => setShowStopConfirm(false)}
      />
      <ConfirmDialog
        open={showStartConfirm}
        title="Start the IB Gateway?"
        body="This starts the IB Gateway and brings the API, relay, and monitor back in order. Starting the gateway triggers one IBKR Mobile 2FA push to your phone. Approve it promptly, and approve only ONE push to avoid stacking. This takes about 60 to 90 seconds and the page will briefly lose its connection while FastAPI cycles."
        confirmLabel="Start Gateway"
        pending={pendingPower}
        onConfirm={runStart}
        onCancel={() => setShowStartConfirm(false)}
      />
    </section>
  );
}
