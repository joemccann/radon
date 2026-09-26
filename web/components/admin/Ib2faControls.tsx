"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { AdminHealthPayload, HostRole, UnitStatus } from "@/lib/adminTypes";
import {
  forcePushDisabledReason,
  gatewayPowerState,
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
  onRestartStack: () => Promise<boolean | void>;
  /** The radon-ib-gateway.service unit row (filtered from the table, still
   *  surfaced here for the power control's active_state). */
  gatewayUnit?: UnitStatus | null;
  /** Whether the host supports systemctl control (false off-VPS). */
  servicesSupported?: boolean;
  /** Targeted per-unit stop of the gateway (control_unit / systemctl stop). */
  onStopGateway?: () => Promise<boolean>;
  /** Targeted per-unit restart of the gateway. */
  onRestartGateway?: () => Promise<boolean>;
  /** Start the gateway through its existing recovery handler. */
  onStartGateway?: () => Promise<boolean>;
  /** Both admin polls (/admin/services + /health) are failing — radon-api is
   *  unreachable, which is exactly what a gateway stop causes (cascade). The
   *  cached unit row then reads a stale value; treat the gateway as unknown. */
  apiUnreachable?: boolean;
  /** App hosts must not cycle the broker Gateway. Unset means combined. */
  hostRole?: HostRole;
  onAfter?: () => void;
  /** Hoist the suggested recovery trigger while preserving one control owner. */
  primaryActionContainer?: HTMLElement | null;
  /** Hoist the existing stack trigger into service controls. */
  stackActionContainer?: HTMLElement | null;
  primaryObservationCurrent?: boolean;
  /** Shared workspace lock for gateway and service commands. */
  externalPending?: boolean;
  onInspect?: () => void;
};

/**
 * One owner for gateway lifecycle, 2FA recovery and stack restart commands.
 * Header/attention triggers share these confirmations and pending guards.
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
  onRestartGateway,
  apiUnreachable = false,
  hostRole,
  onAfter,
  primaryActionContainer = null,
  stackActionContainer = null,
  primaryObservationCurrent = true,
  externalPending = false,
  onInspect,
}: Ib2faControlsProps) {
  const [showForceConfirm, setShowForceConfirm] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [showStartConfirm, setShowStartConfirm] = useState(false);
  const [showGatewayRestartConfirm, setShowGatewayRestartConfirm] = useState(false);
  const [pendingForce, setPendingForce] = useState(false);
  const [pendingReset, setPendingReset] = useState(false);
  const [pendingRestart, setPendingRestart] = useState(false);
  const [pendingPower, setPendingPower] = useState(false);
  // Optimistic power target set on a confirmed Stop/Start so the button flips
  // immediately rather than waiting for the next /admin/services poll. Cleared
  // once the poll settles to the expected terminal state (or after the safety
  // window) so it never sticks stale.
  const [optimisticPower, setOptimisticPower] = useState<GatewayPowerState | null>(null);

  const anyPending = externalPending || pendingForce || pendingReset || pendingRestart || pendingPower;
  const pushLock = health?.ib_gateway?.restart_backoff?.push_lock ?? null;

  // When radon-api is unreachable (both admin polls failing — the cascade a
  // gateway stop causes), the cached unit row is not affirmative power-state
  // evidence. Fail closed as unknown and disable destructive power actions.
  const polledPowerState: GatewayPowerState = apiUnreachable || !primaryObservationCurrent ||
    gatewayUnit?.active_state === "unknown" || (!gatewayUnit && health?.ib_gateway?.port_listening == null)
    ? "unknown"
    : gatewayPowerState({
        unit: gatewayUnit,
        portListening: health?.ib_gateway?.port_listening,
      });
  const powerState = polledPowerState === "unknown" ? "unknown" : optimisticPower ?? polledPowerState;

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

  const remoteGateway = hostRole === "app" && gatewayUnit?.can_control === true;
  const ownsGatewayLifecycle = hostRole !== "app" || remoteGateway;
  const stackCyclesGateway = hostRole !== "app";
  const gatewayDependents = unitDependents(GATEWAY_UNIT);
  // Start triggers a fresh 2FA login, so gate it on the same push lock as
  // Force 2FA to keep two pushes from racing (feedback_2fa_push_stacking).
  const lifecycleDisabledReason = !primaryObservationCurrent || apiUnreachable
    ? "Refresh gateway status before running a command."
    : !ownsGatewayLifecycle || gatewayUnit?.can_control === false
      ? "Read-only: this host cannot control the broker Gateway."
      : !servicesSupported && !remoteGateway
        ? "Read-only: this browser is not on the Hetzner VPS."
        : null;
  const transitionDisabledReason = powerState === "unknown"
    ? "Gateway state is unknown while the control plane is unreachable."
    : powerState === "transitional"
      ? "Gateway is mid-transition. Wait for it to settle."
      : null;
  const pushDisabledReason = forcePushDisabledReason({ pushLock, pending: false });
  const startDisabledReason = lifecycleDisabledReason ?? transitionDisabledReason ??
    (powerState === "running" ? "Gateway is already running." : !onStartGateway ? "Gateway start control is unavailable." : pushDisabledReason);
  const stopDisabledReason = lifecycleDisabledReason ?? transitionDisabledReason ??
    (powerState === "stopped" ? "Gateway is already stopped." : !onStopGateway ? "Gateway stop control is unavailable." : null);
  const restartGatewayDisabledReason = lifecycleDisabledReason ?? transitionDisabledReason ??
    (!onRestartGateway ? "Gateway restart control is unavailable." : pushDisabledReason);
  const powerDisabledReason = powerState === "stopped" ? startDisabledReason : stopDisabledReason;
  const powerDisabled = anyPending || powerDisabledReason !== null;
  const disableReason = lifecycleDisabledReason ?? transitionDisabledReason ??
    forcePushDisabledReason({ pushLock, pending: anyPending });
  const disableForce = disableReason !== null;
  const stackDisabledReason = !primaryObservationCurrent || apiUnreachable
    ? "Refresh service status before running a command."
    : !servicesSupported
      ? "Read-only: service control is unavailable on this host."
      : stackCyclesGateway ? lifecycleDisabledReason ?? transitionDisabledReason ?? pushDisabledReason : null;
  const resetDisabled = anyPending || !primaryObservationCurrent || apiUnreachable;

  const runForce = async () => {
    if (anyPending || disableForce) return;
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
    if (resetDisabled) return;
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
    if (anyPending || stackDisabledReason) return;
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
    if (anyPending || stopDisabledReason) return;
    setPendingPower(true);
    try {
      const succeeded = onStopGateway ? await onStopGateway() : false;
      if (succeeded) {
        // Flip only after the control plane explicitly acknowledges success.
        setOptimisticPower("stopped");
      }
    } finally {
      setPendingPower(false);
      setShowStopConfirm(false);
      onAfter?.();
    }
  };

  const runStart = async () => {
    if (anyPending || startDisabledReason) return;
    setPendingPower(true);
    try {
      const succeeded = onStartGateway ? await onStartGateway() : false;
      if (succeeded) {
        // Start is a full-stack restart (~60-90s); show it as in-transition
        // until the poll confirms the gateway is back up.
        setOptimisticPower("transitional");
      }
    } finally {
      setPendingPower(false);
      setShowStartConfirm(false);
      onAfter?.();
    }
  };

  const runGatewayRestart = async () => {
    if (anyPending || restartGatewayDisabledReason || !onRestartGateway) return;
    setPendingPower(true);
    try {
      if (await onRestartGateway()) setOptimisticPower("transitional");
    } finally {
      setPendingPower(false);
      setShowGatewayRestartConfirm(false);
      onAfter?.();
    }
  };

  const authState = health?.ib_gateway?.auth_state;
  const powerStatusLine =
    powerState === "running"
      ? authState === "authenticated"
        ? "Gateway is running. IB data plane live."
        : authState === "awaiting_2fa"
          ? "Gateway is up. Approve the IBKR Mobile push."
          : "Gateway process is up. IB is not authenticated."
      : powerState === "transitional"
        ? "Gateway is mid-transition."
        : powerState === "unknown"
          ? "Gateway state is unknown. Control plane unavailable."
        : "Gateway is stopped. IB, orders relay, and monitor are offline.";

  const powerButtonLabel = pendingPower
    ? "Working..."
    : powerState === "transitional"
      ? gatewayUnit?.active_state === "deactivating"
        ? "Stopping..."
        : "Starting..."
      : powerState === "running"
        ? "Stop Gateway"
        : powerState === "stopped"
          ? "Start Gateway"
          : "Unavailable";

  const suggestStart = primaryObservationCurrent && ownsGatewayLifecycle && powerState === "stopped" && Boolean(onStartGateway);
  const suggestPush = primaryObservationCurrent && ownsGatewayLifecycle && !apiUnreachable && powerState === "running" &&
    (authState === "awaiting_2fa" || authState === "unreachable");
  const primaryDisabledReason = suggestStart ? powerDisabledReason : suggestPush ? disableReason : null;
  const primaryDisabled = anyPending || (suggestStart ? powerDisabled : suggestPush ? disableForce : !onInspect);

  const stackAction = (
    <button
      type="button"
      className="admin-btn admin-btn-danger"
      onClick={() => setShowRestartConfirm(true)}
      disabled={anyPending || stackDisabledReason !== null}
      title={stackDisabledReason ?? (
        stackCyclesGateway
          ? "Run radon restart on the VPS: stops then starts every radon-* unit in order"
          : "Run radon restart on this app host. Gateway stays on the broker."
      )}
      data-testid="restart-stack-button"
    >
      {pendingRestart ? "Restarting..." : "Restart All Services"}
    </button>
  );

  return (
    <section className="admin-card" data-testid="ib-controls">
      {primaryActionContainer && createPortal(
        <>
          <button
            type="button"
            className="admin-btn admin-btn-primary"
            data-testid="admin-primary-recovery"
            disabled={primaryDisabled}
            aria-describedby={primaryDisabledReason ? "admin-primary-recovery-reason" : undefined}
            onClick={() => suggestStart ? setShowStartConfirm(true) : suggestPush ? setShowForceConfirm(true) : onInspect?.()}
          >
            {anyPending ? "Working..." : suggestStart ? "Start Gateway" : suggestPush ? "Force 2FA Push" : "Review gateway"}
          </button>
          {primaryDisabledReason && <p className="admin-card-note" id="admin-primary-recovery-reason">{primaryDisabledReason}</p>}
        </>,
        primaryActionContainer,
      )}
      <header className="admin-card-header">
        <span className="admin-card-title">IB Gateway controls</span>
      </header>

      {ownsGatewayLifecycle ? (
      <div className="admin-gateway-power" data-testid="gateway-power">
        <span className="admin-card-note-inline">Gateway power</span>
        <p
          className="admin-gateway-power-status"
          data-testid="gateway-power-status"
          data-state={powerState}
        >
          {powerStatusLine}
        </p>
        <div className="admin-actions-row">
          <button
            type="button"
            className="admin-btn admin-btn-primary admin-gateway-power-btn"
            onClick={() => setShowStartConfirm(true)}
            disabled={anyPending || startDisabledReason !== null}
            title={startDisabledReason ?? "Start IB Gateway and request one 2FA approval"}
            data-testid={powerState === "stopped" ? "gateway-power-button" : "gateway-start-button"}
          >
            {powerState === "stopped" ? powerButtonLabel : "Start Gateway"}
          </button>
          <button
            type="button"
            className="admin-btn admin-btn-ghost admin-gateway-power-btn"
            onClick={() => setShowGatewayRestartConfirm(true)}
            disabled={anyPending || restartGatewayDisabledReason !== null}
            title={restartGatewayDisabledReason ?? "Restart IB Gateway and request a fresh 2FA approval"}
            data-testid="gateway-restart-button"
          >
            Restart Gateway
          </button>
          <button
            type="button"
            className="admin-btn admin-btn-danger admin-gateway-power-btn"
            onClick={() => setShowStopConfirm(true)}
            disabled={anyPending || stopDisabledReason !== null}
            title={stopDisabledReason ?? "Stop IB Gateway"}
            data-testid={powerState !== "stopped" ? "gateway-power-button" : "gateway-stop-button"}
          >
            {powerState !== "stopped" ? powerButtonLabel : "Stop Gateway"}
          </button>
        </div>
        {powerDisabledReason && (
          <p className="admin-card-note" data-testid="gateway-power-disabled-reason">
            {powerDisabledReason}
          </p>
        )}
      </div>
      ) : null}

      <div className="admin-actions-row">
        {ownsGatewayLifecycle ? (
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
        ) : null}

        <button
          type="button"
          className="admin-btn admin-btn-ghost"
          onClick={() => setShowResetConfirm(true)}
          disabled={resetDisabled}
          title={
            hostRole === "app"
              ? "Release the broker's 2FA push lease and clear the restart backoff counter"
              : "Release the push lock and clear the restart backoff counter"
          }
          data-testid="reset-backoff-button"
        >
          Reset Backoff
        </button>

        {stackActionContainer ? createPortal(stackAction, stackActionContainer) : stackAction}
      </div>

      {ownsGatewayLifecycle && disableReason && (
        <p className="admin-card-note" data-testid="force-2fa-disabled-reason">
          {disableReason}
        </p>
      )}

      {!ownsGatewayLifecycle ? (
        <p className="admin-card-note" data-testid="gateway-broker-note">
          Gateway lifecycle is on the broker. This app host cannot Force 2FA, stop, or start IB Gateway. SSH to the broker and run radon-ib-gateway-control.
        </p>
      ) : null}

      <ConfirmDialog
        open={showForceConfirm}
        title="Force 2FA push?"
        body="This fires an IBKR Mobile 2FA push to your phone. Approve quickly to avoid stacking another push and confusing the IBKR backend."
        confirmLabel="Send push"
        destructive
        pending={pendingForce}
        confirmDisabled={anyPending || disableForce}
        disabledReason={disableReason ?? (anyPending ? "Another command is in progress." : undefined)}
        onConfirm={runForce}
        onCancel={() => setShowForceConfirm(false)}
      />
      <ConfirmDialog
        open={showResetConfirm}
        title={hostRole === "app" ? "Release the broker's push lease?" : "Reset restart backoff?"}
        body={
          hostRole === "app"
            ? "Use only after manually approving the in-flight 2FA push outside the lock window. This clears the backoff counter AND releases the 2FA push lease on the broker over mTLS; the broker refuses a fresh login for 60s afterwards so two pushes cannot stack."
            : "Use only after manually approving the in-flight 2FA push outside the lock window. This clears the backoff counter and releases the push lock so the next legitimate restart fires immediately."
        }
        confirmLabel="Reset"
        pending={pendingReset}
        confirmDisabled={resetDisabled}
        disabledReason={anyPending ? "Another command is in progress." : "Refresh gateway status before running a command."}
        onConfirm={runReset}
        onCancel={() => setShowResetConfirm(false)}
      />
      <ConfirmDialog
        open={showRestartConfirm}
        title="Restart all radon services?"
        body={
          stackCyclesGateway
            ? "Runs radon restart on the VPS: stops every radon-* systemd unit, then starts them in dependency order (IB Gateway first). Takes about 60 to 90 seconds. The page will briefly lose its connection while FastAPI cycles. IB Gateway will need a fresh 2FA approval on your phone when it comes back up."
            : "Runs radon restart on this app host. App-plane units only. Gateway stays on the broker. The page will briefly lose its connection while FastAPI cycles."
        }
        confirmLabel="Restart all"
        destructive
        pending={pendingRestart}
        confirmDisabled={anyPending || stackDisabledReason !== null}
        disabledReason={stackDisabledReason ?? (anyPending ? "Another command is in progress." : undefined)}
        onConfirm={runRestart}
        onCancel={() => setShowRestartConfirm(false)}
      />
      <ConfirmDialog
        open={showStopConfirm}
        title="Stop the IB Gateway?"
        body="This stops the IB Gateway. IB data and orders go offline. App services stay up. Start Gateway brings the Gateway back and fires one 2FA push."
        confirmLabel="Stop Gateway"
        destructive
        affectedUnits={gatewayDependents}
        requireTyped={GATEWAY_UNIT}
        pending={pendingPower}
        confirmDisabled={anyPending || stopDisabledReason !== null}
        disabledReason={stopDisabledReason ?? (anyPending ? "Another command is in progress." : undefined)}
        onConfirm={runStop}
        onCancel={() => setShowStopConfirm(false)}
      />
      <ConfirmDialog
        open={showGatewayRestartConfirm}
        title="Restart the IB Gateway?"
        body={remoteGateway
          ? "This restarts only the broker Gateway and fires one fresh IBKR Mobile 2FA push. IB data and orders go offline during recovery. Approve only one push. App services stay up."
          : "This restarts only the IB Gateway and fires one fresh IBKR Mobile 2FA push. IB data and orders go offline during recovery. Approve only one push. Dependent relay and monitor services stop; use Restart All Services to recover them."
        }
        confirmLabel="Restart Gateway"
        destructive
        affectedUnits={remoteGateway ? [] : gatewayDependents}
        requireTyped={GATEWAY_UNIT}
        pending={pendingPower}
        confirmDisabled={anyPending || restartGatewayDisabledReason !== null}
        disabledReason={restartGatewayDisabledReason ?? (anyPending ? "Another command is in progress." : undefined)}
        onConfirm={runGatewayRestart}
        onCancel={() => setShowGatewayRestartConfirm(false)}
      />
      <ConfirmDialog
        open={showStartConfirm}
        title="Start the IB Gateway?"
        body="This starts the IB Gateway. It fires one IBKR Mobile 2FA push. Approve only one. App services stay up."
        confirmLabel="Start Gateway"
        pending={pendingPower}
        confirmDisabled={anyPending || startDisabledReason !== null}
        disabledReason={startDisabledReason ?? (anyPending ? "Another command is in progress." : undefined)}
        onConfirm={runStart}
        onCancel={() => setShowStartConfirm(false)}
      />
    </section>
  );
}
