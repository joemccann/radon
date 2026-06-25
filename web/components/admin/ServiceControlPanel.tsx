"use client";

import { useState } from "react";
import type {
  ServiceAction,
  ServicesListResponse,
  UnitStatus,
} from "@/lib/adminTypes";
import { useSort } from "@/lib/useSort";
import {
  serviceControlDisabledReason,
  unitActivityLabel,
  unitDependents,
  unitVerdict,
} from "@/lib/adminFormat";
import type { FlashTarget } from "./AdminWorkspace";
import ConfirmDialog from "./ConfirmDialog";
import SortTh from "../SortTh";

type ServiceControlPanelProps = {
  services: ServicesListResponse | null;
  loading: boolean;
  error: string | null;
  onAction: (unit: string, action: ServiceAction) => Promise<void>;
  flashTarget?: FlashTarget | null;
};

type PendingAction = { unit: string; action: ServiceAction } | null;
type ServiceSortKey = "status" | "unit" | "activity";

// IB Gateway is managed in its own panel above; don't show it twice.
const HIDDEN_FROM_TABLE = new Set(["radon-ib-gateway.service"]);

/**
 * Service Control: every controllable radon-* systemd unit with a clear
 * one-word status verdict and discoverable, safe start/stop/restart controls.
 * Start fires immediately; Restart confirms lightly; Stop is high-severity
 * (enumerates cascade dependents + type-to-confirm for units that have them).
 */
export default function ServiceControlPanel({
  services,
  loading,
  error,
  onAction,
  flashTarget = null,
}: ServiceControlPanelProps) {
  const [pending, setPending] = useState<PendingAction>(null);
  const [confirm, setConfirm] = useState<PendingAction>(null);

  const runAction = async (unit: string, action: ServiceAction) => {
    setPending({ unit, action });
    try {
      await onAction(unit, action);
    } finally {
      setPending(null);
      setConfirm(null);
    }
  };

  const requestAction = (unit: string, action: ServiceAction) => {
    if (action === "start") void runAction(unit, action);
    else setConfirm({ unit, action });
  };

  const supported = services?.supported ?? false;
  const units = (services?.units ?? []).filter((u) => !HIDDEN_FROM_TABLE.has(u.unit));
  const { sorted: sortedUnits, sort, toggle } = useSort<UnitStatus, ServiceSortKey>(units, serviceSortValue);
  const dependents = confirm?.action === "stop" ? unitDependents(confirm.unit) : [];
  const stopNeedsTyped = dependents.length > 0;

  if (loading && !services) {
    return (
      <section className="admin-card" data-testid="services-card">
        <header className="admin-card-header">
          <span className="admin-card-title">Service Control</span>
        </header>
        <div className="admin-table-scroll">
        <table className="admin-services-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Unit</th>
              <th>Activity</th>
              <th className="admin-col-controls">Controls</th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 6 }).map((_, i) => (
              <tr key={i} className="admin-skeleton-row">
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 80 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 150 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 90 }} /></td>
                <td className="admin-col-controls"><span className="admin-skeleton admin-skeleton-line" style={{ width: 130, height: 22 }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>
    );
  }

  if (error && !services) {
    return (
      <section className="admin-card" data-testid="services-card">
        <header className="admin-card-header">
          <span className="admin-card-title">Service Control</span>
        </header>
        <p className="admin-card-empty admin-card-error">{error}</p>
      </section>
    );
  }

  return (
    <section className="admin-card" data-testid="services-card">
      <header className="admin-card-header">
        <span className="admin-card-title">Service Control</span>
      </header>
      <p className="admin-card-subhead">
        {supported
          ? "systemd units on the Hetzner VPS (the radon-* stack). Controls call systemctl via polkit."
          : "Read-only: this browser is not on the Hetzner VPS, so controls are disabled."}
      </p>

      <div className="admin-table-scroll">
      <table className="admin-services-table">
        <thead>
          <tr>
            <SortTh<ServiceSortKey> label="Status" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<ServiceSortKey> label="Unit" sortKey="unit" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<ServiceSortKey> label="Activity" sortKey="activity" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <th className="admin-col-controls">Controls</th>
          </tr>
        </thead>
        <tbody>
          {sortedUnits.map((unit) => (
            <ServiceRow
              key={unit.unit}
              unit={unit}
              supported={supported}
              pending={pending}
              onRequest={requestAction}
              flashTarget={flashTarget}
            />
          ))}
        </tbody>
      </table>
      </div>

      <p className="admin-services-help">
        Daemon stuck or pool disconnected after 2FA? Use Restart All Services. A
        single scheduled job failed? Restart that row. IB session issues? See IB
        Gateway above.
      </p>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm ? `${capitalize(confirm.action)} ${confirm.unit}?` : ""}
        body={
          confirm
            ? confirm.action === "stop"
              ? `This runs systemctl stop ${confirm.unit}.`
              : `This runs systemctl restart ${confirm.unit}. Brief downtime while it restarts.`
            : ""
        }
        confirmLabel={confirm ? capitalize(confirm.action) : ""}
        destructive={confirm?.action === "stop"}
        affectedUnits={stopNeedsTyped ? dependents : undefined}
        requireTyped={stopNeedsTyped ? confirm!.unit : undefined}
        pending={pending !== null}
        onConfirm={() => confirm && runAction(confirm.unit, confirm.action)}
        onCancel={() => setConfirm(null)}
      />
    </section>
  );
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function serviceSortValue(unit: UnitStatus, key: ServiceSortKey): string | number | null {
  switch (key) {
    case "status":
      return unitVerdict(unit).label;
    case "unit":
      return unit.unit;
    case "activity":
      return unitActivityLabel(unit);
  }
}

function ServiceRow({
  unit,
  supported,
  pending,
  onRequest,
  flashTarget,
}: {
  unit: UnitStatus;
  supported: boolean;
  pending: PendingAction;
  onRequest: (unit: string, action: ServiceAction) => void;
  flashTarget: FlashTarget | null;
}) {
  const verdict = unitVerdict(unit);
  const isUnitPending = pending?.unit === unit.unit;
  const activity = unitActivityLabel(unit);
  const flashClass = resolveFlashClass(unit.unit, flashTarget);
  const rawState = `${unit.active_state} (${unit.sub_state})`;
  const transitional = verdict.label === "Starting" || verdict.label === "Stopping";

  return (
    <tr
      data-testid={`service-row-${unit.unit}`}
      className={flashClass}
      data-flash={flashClass ? "true" : undefined}
    >
      <td>
        <div className="admin-verdict-cell" title={rawState}>
          <span
            className={`admin-status-dot admin-status-dot-${verdict.tone}${transitional ? " admin-status-dot-pulse" : ""}`}
            aria-hidden
          />
          <span className="admin-verdict-label">{verdict.label}</span>
        </div>
      </td>
      <td>
        <div className="admin-unit-name">{unit.unit}</div>
        {unit.description && <div className="admin-unit-desc">{unit.description}</div>}
      </td>
      <td>
        <div className="admin-unit-activity" data-testid={`service-activity-${unit.unit}`}>
          {activity}
        </div>
      </td>
      <td className="admin-col-controls">
        <div className="admin-row-actions">
          {(["start", "restart", "stop"] as ServiceAction[]).map((action) => {
            const reason = serviceControlDisabledReason({
              unit,
              action,
              supported,
              pending: isUnitPending && pending?.action === action,
            });
            const disabled = reason !== null;
            const inFlight = isUnitPending && pending?.action === action;
            return (
              <button
                key={action}
                type="button"
                className={`admin-btn admin-btn-sm ${action === "stop" ? "admin-btn-danger" : action === "restart" ? "admin-btn-primary" : "admin-btn-ghost"}`}
                disabled={disabled}
                title={reason ?? undefined}
                onClick={() => onRequest(unit.unit, action)}
                data-testid={`service-${action}-${unit.unit}`}
              >
                {inFlight ? "..." : capitalize(action)}
              </button>
            );
          })}
        </div>
      </td>
    </tr>
  );
}

/** Class name added to a row that was just acted on (success or failure). */
function resolveFlashClass(unit: string, flash: FlashTarget | null): string {
  if (!flash || flash.unit !== unit) return "";
  return flash.ok ? "admin-row-flash" : "admin-row-flash-error";
}
