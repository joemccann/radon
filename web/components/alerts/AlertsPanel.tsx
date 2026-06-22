"use client";

import { useState } from "react";
import { useAlerts, type AlertRule } from "@/lib/useAlerts";

/**
 * AlertsPanel — user-scoped signal alert rules. Each rule is a simple
 * (metric op threshold) predicate evaluated against the next scan row for a
 * ticker; matches dispatch through the existing watchdog notifier. Mirrors the
 * bookmarks/watchlist CRUD pattern. Data via `/api/alerts`.
 */

const OPS = [">", "<", ">=", "<="] as const;
const METRICS = ["flow_strength", "score", "buy_ratio"] as const;

function chipStyle(token: string): React.CSSProperties {
  return {
    color: token,
    backgroundColor: `color-mix(in srgb, ${token} 14%, transparent)`,
    borderRadius: 4,
    padding: "1px 6px",
    fontFamily: "var(--font-mono)",
    fontSize: 10,
    letterSpacing: "0.06em",
  };
}

function RuleRow({ rule, onDelete }: { rule: AlertRule; onDelete: (id: string) => void }) {
  return (
    <li className="snapshot-row">
      <span style={chipStyle("var(--signal-core)")}>{rule.ticker}</span>
      <span className="snapshot-row__signal">
        {rule.metric} {rule.op} {rule.threshold}
      </span>
      <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
        {rule.channel}
      </span>
      <button
        type="button"
        aria-label="Delete alert rule"
        className="snapshot-row__action"
        onClick={() => onDelete(rule.id)}
        style={{
          marginLeft: "auto",
          color: "var(--negative)",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontSize: 12,
        }}
      >
        Remove
      </button>
    </li>
  );
}

export function AlertsPanel() {
  const { rules, isLoading, error, createRule, deleteRule } = useAlerts();
  const [ticker, setTicker] = useState("");
  const [metric, setMetric] = useState<string>(METRICS[0]);
  const [op, setOp] = useState<string>(OPS[0]);
  const [threshold, setThreshold] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const canSubmit = ticker.trim().length > 0 && threshold.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const value = Number(threshold);
    if (!Number.isFinite(value)) {
      setFormError("Threshold must be a number");
      return;
    }
    setSubmitting(true);
    try {
      await createRule({ ticker: ticker.trim().toUpperCase(), metric, op, threshold: value });
      setTicker("");
      setThreshold("");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create alert rule");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteRule(id);
    } catch {
      // load() inside the hook keeps the list authoritative on next render
    }
  }

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Alerts</p>
        <h3 className="panel-title">Signal Alert Rules</h3>
      </header>

      <form onSubmit={handleSubmit} className="alerts-form" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 10, color: "var(--text-muted)" }}>
          Ticker
          <input
            aria-label="Ticker"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="AAPL"
            style={{ fontFamily: "var(--font-mono)" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 10, color: "var(--text-muted)" }}>
          Metric
          <select aria-label="Metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 10, color: "var(--text-muted)" }}>
          Operator
          <select aria-label="Operator" value={op} onChange={(e) => setOp(e.target.value)}>
            {OPS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 10, color: "var(--text-muted)" }}>
          Threshold
          <input
            aria-label="Threshold"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            inputMode="decimal"
            placeholder="70"
            style={{ fontFamily: "var(--font-mono)" }}
          />
        </label>
        <button type="submit" disabled={!canSubmit} style={{ alignSelf: "flex-end" }}>
          Add rule
        </button>
      </form>

      {formError ? <div className="snapshot-card__error">{formError}</div> : null}

      {isLoading ? (
        <div className="snapshot-card__empty">Loading…</div>
      ) : error ? (
        <div className="snapshot-card__error">{error}</div>
      ) : rules.length === 0 ? (
        <div className="snapshot-card__empty">No alert rules yet</div>
      ) : (
        <ul className="snapshot-rows">
          {rules.map((rule) => (
            <RuleRow key={rule.id} rule={rule} onDelete={handleDelete} />
          ))}
        </ul>
      )}
    </section>
  );
}
