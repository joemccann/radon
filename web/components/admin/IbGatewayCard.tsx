"use client";

import RequestError from "@/components/RequestError";

import { useMemo } from "react";
import type { AdminHealthPayload } from "@/lib/adminTypes";
import {
  authStateLabel,
  authStateTone,
  backoffSummary,
} from "@/lib/adminFormat";

type IbGatewayCardProps = {
  health: AdminHealthPayload | null;
  loading: boolean;
  error: string | null;
};

/**
 * Read-only snapshot of the IB Gateway. Sourced from FastAPI /health.
 * Pure render — all live state is owned by the page above.
 */
export default function IbGatewayCard({ health, loading, error }: IbGatewayCardProps) {
  const gateway = health?.ib_gateway;
  const pool = health?.ib_pool ?? {};
  const tone = authStateTone(gateway?.auth_state);
  const backoff = gateway?.restart_backoff;

  const poolRows = useMemo(() => Object.entries(pool), [pool]);
  const accounts = useMemo(
    () => Array.from(new Set(poolRows.flatMap(([, info]) => info.managed_accounts ?? []))),
    [poolRows],
  );

  if (loading && !health) {
    return (
      <section className="admin-card" data-testid="ib-gateway-card">
        <header className="admin-card-header">
          <span className="admin-card-title">IB Gateway</span>
        </header>
        <dl className="admin-kv">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i}>
              <dt><span className="admin-skeleton admin-skeleton-line" style={{ width: 48 }} /></dt>
              <dd><span className="admin-skeleton admin-skeleton-line" style={{ width: 92 }} /></dd>
            </div>
          ))}
        </dl>
      </section>
    );
  }

  if (error && !health) {
    return (
      <section className="admin-card" data-testid="ib-gateway-card">
        <header className="admin-card-header">
          <span className="admin-card-title">IB Gateway</span>
        </header>
        <RequestError error={error} fallback="Gateway status could not be loaded. Try again." />
      </section>
    );
  }

  return (
    <section className="admin-card" data-testid="ib-gateway-card">
      <RequestError error={error} fallback="Gateway status could not be refreshed. Try again." retainedData={health != null} />
      <header className="admin-card-header">
        <span className="admin-card-title">IB Gateway</span>
        <span
          className={`admin-pill admin-pill-${tone}`}
          data-testid="ib-auth-state"
          data-auth-state={gateway?.auth_state}
        >
          {authStateLabel(gateway?.auth_state)}
        </span>
      </header>

      <dl className="admin-kv">
        <div>
          <dt>Mode</dt>
          <dd>{gateway?.gateway_mode ?? "unknown"}</dd>
        </div>
        <div>
          <dt>Host</dt>
          <dd>
            {gateway?.host ?? "?"}:{gateway?.port ?? "?"}
          </dd>
        </div>
        <div>
          <dt>Port</dt>
          <dd>{gateway?.port_listening ? "listening" : "closed"}</dd>
        </div>
        <div>
          <dt>Container</dt>
          <dd>
            {gateway?.container_state ?? "n/a"}
            {gateway?.container_health ? ` / ${gateway.container_health}` : ""}
          </dd>
        </div>
        <div>
          <dt>Backoff</dt>
          <dd data-testid="ib-backoff-summary">{backoffSummary(backoff)}</dd>
        </div>
        <div>
          <dt>Push lock</dt>
          <dd data-testid="ib-push-lock">
            {backoff?.push_lock
              ? `held by ${backoff.push_lock.holder} (${backoff.push_lock.remaining_secs}s)`
              : "free"}
          </dd>
        </div>
      </dl>

      {poolRows.length > 0 && (
        <div className="admin-pool" data-testid="ib-pool-table">
          <div className="admin-pool-accounts">
            <span className="admin-pool-accounts-label">Account</span>
            {accounts.join(", ") || "none"}
          </div>
          <div className="admin-pool-roles">
            {poolRows.map(([role, info]) => (
              <span
                key={role}
                className="admin-pool-role"
                title={`client ${info.client_id} · ${info.connected ? "connected" : "disconnected"}`}
              >
                <span
                  className={`admin-status-dot admin-status-dot-${info.connected ? "positive" : "negative"}`}
                  aria-hidden
                />
                {role}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
