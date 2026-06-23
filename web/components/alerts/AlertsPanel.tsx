"use client";

import { useState } from "react";
import { useAlerts, type AlertRule } from "@/lib/useAlerts";
import { formatRelativeTime } from "@/lib/adminFormat";

/**
 * AlertsPanel — user-scoped signal alert rules. Each rule is a simple
 * (metric op threshold) predicate evaluated against the next scan row for a
 * ticker; matches dispatch through the existing watchdog notifier. Mirrors the
 * bookmarks/watchlist CRUD pattern. Data via `/api/alerts`.
 */

const OPS = [">", "<", ">=", "<="] as const;

/** Metric registry: human label, valid threshold range (so a buy_ratio rule
 *  can't be saved with a 0-100 score threshold that can never fire), and a
 *  representative placeholder. `value` stays the raw snake_case key the engine
 *  reads; only the displayed label is humanised. */
const METRIC_META: Record<string, { label: string; range: [number, number]; placeholder: string }> = {
  flow_strength: { label: "Flow Strength", range: [0, 100], placeholder: "70" },
  score: { label: "Signal Score", range: [0, 100], placeholder: "70" },
  buy_ratio: { label: "Buy Ratio", range: [0, 1], placeholder: "0.65" },
};
const METRICS = Object.keys(METRIC_META);

const CHANNEL_LABELS: Record<string, string> = {
  pushover: "Pushover",
  service_health: "Status banner",
};
const CHANNELS = Object.keys(CHANNEL_LABELS);

const RECENT_FIRE_MS = 24 * 60 * 60 * 1000;

function metricLabel(metric: string): string {
  return METRIC_META[metric]?.label ?? metric;
}
function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel;
}

function RuleRow({ rule, onDelete }: { rule: AlertRule; onDelete: (id: string) => void }) {
  const firedAt = rule.last_fired_at ? Date.parse(rule.last_fired_at) : NaN;
  const recent = Number.isFinite(firedAt) && Date.now() - firedAt < RECENT_FIRE_MS;
  const firedLabel = rule.last_fired_at ? `Fired ${formatRelativeTime(rule.last_fired_at)}` : "Never fired";
  const predicate = `${metricLabel(rule.metric)} ${rule.op} ${rule.threshold}`;
  return (
    <li className="snapshot-row alerts-rule">
      <span className="alerts-rule__chip">{rule.ticker}</span>
      <span className="snapshot-row__signal">{predicate}</span>
      <span className={`alerts-rule__fired${recent ? " alerts-rule__fired--recent" : ""}`}>{firedLabel}</span>
      <span className="alerts-rule__channel">{channelLabel(rule.channel)}</span>
      <button
        type="button"
        aria-label={`Remove alert ${rule.ticker} ${predicate}`}
        className="snapshot-row__action"
        onClick={() => onDelete(rule.id)}
      >
        Remove
      </button>
    </li>
  );
}

export function AlertsPanel() {
  const { rules, isLoading, error, retry, createRule, deleteRule } = useAlerts();
  const [ticker, setTicker] = useState("");
  const [metric, setMetric] = useState<string>(METRICS[0]);
  const [op, setOp] = useState<string>(OPS[0]);
  const [threshold, setThreshold] = useState("");
  const [channel, setChannel] = useState<string>(CHANNELS[0]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const meta = METRIC_META[metric];
  const canSubmit = ticker.trim().length > 0 && threshold.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const value = Number(threshold);
    if (!Number.isFinite(value)) {
      setFormError("Threshold must be a number.");
      return;
    }
    if (meta && (value < meta.range[0] || value > meta.range[1])) {
      setFormError(`${meta.label} threshold must be between ${meta.range[0]} and ${meta.range[1]}.`);
      return;
    }
    setSubmitting(true);
    try {
      await createRule({ ticker: ticker.trim().toUpperCase(), metric, op, threshold: value, channel });
      setTicker("");
      setThreshold("");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create alert rule");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    setDeleteError(null);
    try {
      await deleteRule(id);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete alert rule");
    }
  }

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Alerts</p>
        <h3 className="panel-title">Signal Alert Rules</h3>
        <span className="snapshot-card__see-all">
          {rules.length} {rules.length === 1 ? "RULE" : "RULES"}
        </span>
      </header>

      <form onSubmit={handleSubmit} className="alerts-form">
        <div className="alerts-form__field">
          <span className="alerts-form__label">Ticker</span>
          <input
            aria-label="Ticker"
            className="order-input"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="AAPL"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
          />
        </div>
        <div className="alerts-form__field">
          <span className="alerts-form__label">Metric</span>
          <select aria-label="Metric" className="filter-select" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {METRICS.map((m) => (
              <option key={m} value={m}>
                {metricLabel(m)}
              </option>
            ))}
          </select>
        </div>
        <div className="alerts-form__field">
          <span className="alerts-form__label">Operator</span>
          <select aria-label="Operator" className="filter-select" value={op} onChange={(e) => setOp(e.target.value)}>
            {OPS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div className="alerts-form__field">
          <span className="alerts-form__label">Threshold</span>
          <input
            aria-label="Threshold"
            className="order-input"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            inputMode="decimal"
            placeholder={meta?.placeholder ?? "70"}
          />
        </div>
        <div className="alerts-form__field">
          <span className="alerts-form__label">Channel</span>
          <select aria-label="Channel" className="filter-select" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNELS.map((c) => (
              <option key={c} value={c}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="snapshot-card__see-all snapshot-card__see-all--action alerts-form__submit" disabled={!canSubmit}>
          Add rule
        </button>
      </form>

      {formError ? (
        <div className="snapshot-card__error" role="alert">
          {formError}
        </div>
      ) : null}

      <div role="status" aria-live="polite" aria-atomic="true">
        {deleteError ? <div className="snapshot-card__error">{deleteError}</div> : null}
        {isLoading ? (
          <div className="snapshot-card__empty">Loading rules</div>
        ) : error ? (
          <div className="snapshot-card__error">
            {error}
            <button
              type="button"
              className="snapshot-card__see-all snapshot-card__see-all--action alerts-error__retry"
              onClick={() => void retry()}
            >
              Retry
            </button>
          </div>
        ) : rules.length === 0 ? (
          <div className="snapshot-card__empty">No alert rules yet. Add one above, e.g. AAPL Flow Strength &gt; 70.</div>
        ) : (
          <ul className="snapshot-rows">
            {rules.map((rule) => (
              <RuleRow key={rule.id} rule={rule} onDelete={handleDelete} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
