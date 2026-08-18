"use client";

import { useCallback, useEffect, useState } from "react";
import {
  deriveDemoStatus,
  formatExpiryCountdown,
  sumAiBurn,
  statusTone,
} from "@/lib/demo/demoUsersView";
import type { DemoUserRow } from "@/lib/demo/demoUsers";
import SortTh from "../SortTh";
import { useSort } from "@/lib/useSort";

type DemoSortKey = "email" | "status" | "started" | "expires" | "ai";

function demoExtract(u: DemoUserWithUsage, key: DemoSortKey): string | number | null {
  switch (key) {
    case "email": return u.email ?? u.user_id;
    case "status": return deriveDemoStatus(u);
    case "started": return u.started_at;
    case "expires": return u.expires_at;
    case "ai": return sumAiBurn(u.aiUsage);
    default: return null;
  }
}

type DemoUserWithUsage = DemoUserRow & { aiUsage: Record<string, number> };

/**
 * Operator panel for demo.radon.run trials (Phase 6). Self-fetching: hits the
 * operator-gated /api/admin/demo-users. On a non-demo deployment that route
 * 403s (no ALLOWED_USER_IDS match) or the demo DB is absent — the panel then
 * renders nothing rather than cluttering the prod operator view.
 */
export default function DemoUsersTable() {
  const [users, setUsers] = useState<DemoUserWithUsage[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/demo-users", { cache: "no-store" });
      if (!res.ok) {
        setAvailable(false);
        return;
      }
      const data = (await res.json()) as { users: DemoUserWithUsage[] };
      setUsers(data.users ?? []);
      setAvailable(true);
    } catch {
      setAvailable(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (userId: string, action: "revoke" | "extend") => {
      setBusyId(userId);
      setActionError(null);
      try {
        const response = await fetch("/api/admin/demo-users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, userId, ...(action === "extend" ? { tradingDays: 3 } : {}) }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({})) as { error?: string };
          setActionError(body.error ?? `Action failed (${response.status})`);
          return;
        }
        await load();
      } catch {
        setActionError("Action failed. Access state was not changed.");
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  // Invisible on deployments without a demo backend.
  if (!available) return null;

  return (
    <section className="admin-card" data-testid="demo-users">
      <header className="admin-card-header">
        <span className="admin-card-title">Demo Users</span>
      </header>
      <p className="admin-card-subhead">
        demo.radon.run trials (3 trading days). Today&apos;s AI burn shown; revoke or extend below.
      </p>
      {actionError ? <p className="admin-card-empty" role="alert">{actionError}</p> : null}

      {loading ? (
        <p className="admin-card-empty">Loading demo users…</p>
      ) : !users || users.length === 0 ? (
        <p className="admin-card-empty">No demo trials.</p>
      ) : (
        <div className="admin-table-scroll">
          <DemoUsersDataTable users={users} busyId={busyId} onAct={act} />
        </div>
      )}
    </section>
  );
}

function DemoUsersDataTable({
  users,
  busyId,
  onAct,
}: {
  users: DemoUserWithUsage[];
  busyId: string | null;
  onAct: (userId: string, action: "revoke" | "extend") => void;
}) {
  const { sorted, sort, toggle } = useSort(users, demoExtract);
  return (
    <table className="admin-services-table">
      <thead>
        <tr>
          <SortTh<DemoSortKey> label="Email" sortKey="email" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<DemoSortKey> label="Status" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<DemoSortKey> label="Started" sortKey="started" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<DemoSortKey> label="Expires" sortKey="expires" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<DemoSortKey> label="AI today" sortKey="ai" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((u) => {
          const status = deriveDemoStatus(u);
          return (
            <tr key={u.user_id} data-testid={`demo-user-${u.user_id}`}>
              <td className="admin-unit-name">{u.email ?? u.user_id}</td>
              <td>
                <span className={`admin-pill admin-pill-${statusTone(status)}`}>
                  {status}
                </span>
              </td>
              <td className="admin-unit-activity">{shortDate(u.started_at)}</td>
              <td className="admin-unit-activity">{formatExpiryCountdown(u.expires_at)}</td>
              <td className="admin-unit-activity" title={JSON.stringify(u.aiUsage)}>
                {sumAiBurn(u.aiUsage)}
              </td>
              <td>
                <div className="admin-verdict-cell">
                  <button
                    type="button"
                    className="admin-btn admin-btn-danger admin-btn-small"
                    disabled={busyId === u.user_id || status !== "active"}
                    onClick={() => onAct(u.user_id, "revoke")}
                  >
                    Revoke
                  </button>
                  <button
                    type="button"
                    className="admin-btn admin-btn-ghost admin-btn-small"
                    disabled={busyId === u.user_id}
                    onClick={() => onAct(u.user_id, "extend")}
                  >
                    Extend
                  </button>
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "—";
  return new Date(ms).toISOString().slice(0, 10);
}
