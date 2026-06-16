"use client";

import { ArrowDownRight, ArrowUpRight, Minus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTickerFlowReport, type FlowReportData } from "@/lib/useTickerFlowReport";
import { classifyFlowSignal, type FlowDirection } from "@/lib/flowSignal";
import SignalCard from "@/components/mobile/SignalCard";
import { useViewport } from "@/lib/useViewport";

type Props = {
  ticker: string;
};

export default function TickerFlowReport({ ticker }: Props) {
  const { data, status, error, refresh } = useTickerFlowReport(ticker);
  const verdict = useMemo(() => deriveVerdict(data), [data]);
  const isAnalyzing = status === "loading" || status === "scanning";
  const { isMobile, hasMounted } = useViewport();

  if (hasMounted && isMobile) {
    return (
      <MobileTickerFlowReport
        ticker={ticker}
        data={data}
        verdict={verdict}
        status={status}
        error={error}
        isAnalyzing={isAnalyzing}
        onRefresh={refresh}
      />
    );
  }

  return (
    <div className="ticker-flow-report" data-testid="ticker-flow-report">
      <SignalBadge ticker={ticker} verdict={verdict} status={status} onRefresh={refresh} />

      {error && (
        <div className="section">
          <div className="section-body">
            <div className="alert-item bearish" role="alert">{error}</div>
          </div>
        </div>
      )}

      {isAnalyzing && !data && <AnalyzingPanel ticker={ticker} status={status} />}

      {data && <ReportSections data={data} isAnalyzing={isAnalyzing} />}
    </div>
  );
}

type Verdict = ReturnType<typeof classifyFlowSignal>;

/* ── Mobile ticker flow report ── */

type MobileFlowSection = "overview" | "dark-pool" | "options" | "history";

function flowVerdictTone(direction: FlowDirection | null): "pos" | "neg" | "warn" | "mut" {
  if (direction === "BULLISH") return "pos";
  if (direction === "BEARISH") return "neg";
  return "mut";
}

function formatFlowNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toLocaleString();
}

function MobileTickerFlowReport({
  ticker,
  data,
  verdict,
  status,
  error,
  isAnalyzing,
  onRefresh,
}: {
  ticker: string;
  data: FlowReportData | null;
  verdict: Verdict | null;
  status: ReturnType<typeof useTickerFlowReport>["status"];
  error: string | null;
  isAnalyzing: boolean;
  onRefresh: () => void;
}) {
  const [section, setSection] = useState<MobileFlowSection>("overview");

  const dpAgg = data?.dark_pool?.aggregate ?? {};
  const optionsFlow = data?.options_flow ?? {};
  const daily = (data?.dark_pool?.daily ?? [])
    .slice()
    .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));

  const buyRatio = dpAgg.dp_buy_ratio;
  const buyPct = typeof buyRatio === "number" ? Math.round(buyRatio * 100) : null;
  const sellPct = typeof buyRatio === "number" ? 100 - Math.round(buyRatio * 100) : null;

  const direction = verdict?.direction ?? null;
  const meta = direction === "BULLISH"
    ? { label: "BULLISH", tone: "pos" as const }
    : direction === "BEARISH"
      ? { label: "BEARISH", tone: "neg" as const }
      : { label: "NEUTRAL", tone: "mut" as const };

  const mobileSections: { key: MobileFlowSection; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "dark-pool", label: "Dark Pool" },
    { key: "options", label: "Options" },
    { key: "history", label: "History" },
  ];

  return (
    <div data-testid="ticker-flow-report" style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Mobile header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 16px",
          borderBottom: "1px solid var(--line-grid)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 16,
              fontWeight: 700,
              color: "var(--text-primary)",
            }}
          >
            {ticker}
          </span>
          {verdict && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 4,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                background: `color-mix(in srgb, var(--${meta.tone === "pos" ? "positive" : meta.tone === "neg" ? "negative" : "text-muted"}) 14%, transparent)`,
                color: `var(--${meta.tone === "pos" ? "positive" : meta.tone === "neg" ? "negative" : "text-muted"})`,
                border: `1px solid color-mix(in srgb, var(--${meta.tone === "pos" ? "positive" : meta.tone === "neg" ? "negative" : "text-muted"}) 28%, transparent)`,
              }}
            >
              {meta.label}
            </span>
          )}
          {isAnalyzing && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--warn)" }}>
              {status === "scanning" ? "ANALYZING" : "LOADING"}
            </span>
          )}
        </div>
        <button
          type="button"
          className="tap-target"
          onClick={onRefresh}
          disabled={isAnalyzing}
          aria-label="Refresh flow report"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 600,
            color: isAnalyzing ? "var(--text-muted)" : "var(--signal-core)",
            background: "none",
            border: "none",
            cursor: isAnalyzing ? "default" : "pointer",
            padding: "0 4px",
          }}
        >
          <RefreshCw size={12} style={{ opacity: isAnalyzing ? 0.5 : 1 }} />
          {status === "scanning" ? "Analyzing" : "Refresh"}
        </button>
      </div>

      {error && (
        <div style={{ padding: "8px 16px" }}>
          <div className="alert-item bearish" role="alert">{error}</div>
        </div>
      )}

      {/* Section tabs */}
      <div className="m-segment" role="tablist" aria-label="Flow report sections">
        {mobileSections.map(({ key, label }) => (
          <button
            key={key}
            role="tab"
            aria-selected={section === key}
            type="button"
            className={`m-segment__item${section === key ? " m-segment__item--active" : ""}`}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={{ padding: "12px 16px" }}>
        {section === "overview" && (
          <>
            {!data && isAnalyzing && (
              <div className="alert-item" style={{ textAlign: "center", padding: "24px 0" }}>
                Analyzing {ticker} flow...
              </div>
            )}
            {verdict && (
              <SignalCard
                ticker={ticker}
                score={typeof verdict.confidence === "number" ? verdict.confidence : 0}
                signals={[
                  { label: meta.label, tone: meta.tone },
                  ...(verdict.strength ? [{ label: `${verdict.strength} SIGNAL`, tone: "mut" as const }] : []),
                ]}
                stats={[
                  {
                    label: "Buy Ratio",
                    value: buyPct != null ? `${buyPct}%` : "---",
                  },
                  {
                    label: "Strength",
                    value: dpAgg.flow_strength != null ? String(dpAgg.flow_strength) : "---",
                  },
                  {
                    label: "Prints",
                    value: dpAgg.num_prints != null ? dpAgg.num_prints.toLocaleString() : "---",
                  },
                ]}
              />
            )}
            {verdict?.rationale && (
              <p
                style={{
                  marginTop: 12,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  lineHeight: 1.5,
                }}
              >
                {verdict.rationale}
              </p>
            )}
          </>
        )}

        {section === "dark-pool" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="m-signal-card__stats" style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
              <div className="m-metric">
                <span className="m-metric__label">Direction</span>
                <span className="m-metric__value m-metric__value--primary">
                  {(dpAgg.flow_direction ?? "UNKNOWN").replace("_", " ")}
                </span>
              </div>
              <div className="m-metric">
                <span className="m-metric__label">Strength</span>
                <span className="m-metric__value m-metric__value--primary">
                  {dpAgg.flow_strength ?? "---"}
                </span>
              </div>
              <div className="m-metric">
                <span className="m-metric__label">Buy / Sell</span>
                <span className="m-metric__value m-metric__value--primary">
                  {buyPct != null ? `${buyPct}% / ${sellPct}%` : "---"}
                </span>
              </div>
              <div className="m-metric">
                <span className="m-metric__label">Prints</span>
                <span className="m-metric__value m-metric__value--primary">
                  {dpAgg.num_prints != null ? dpAgg.num_prints.toLocaleString() : "---"}
                </span>
              </div>
              <div className="m-metric">
                <span className="m-metric__label">Volume</span>
                <span className="m-metric__value m-metric__value--primary">
                  {dpAgg.total_volume != null ? formatFlowNumber(dpAgg.total_volume) : "---"}
                </span>
              </div>
              <div className="m-metric">
                <span className="m-metric__label">Premium</span>
                <span className="m-metric__value m-metric__value--primary">
                  {dpAgg.total_premium != null ? `$${formatFlowNumber(dpAgg.total_premium)}` : "---"}
                </span>
              </div>
            </div>
          </div>
        )}

        {section === "options" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
            <div className="m-metric">
              <span className="m-metric__label">Bias</span>
              <span className="m-metric__value m-metric__value--primary">
                {(optionsFlow.bias ?? "NO DATA").replace("_", " ")}
              </span>
            </div>
            <div className="m-metric">
              <span className="m-metric__label">C/P Ratio</span>
              <span className="m-metric__value m-metric__value--primary">
                {optionsFlow.call_put_ratio != null ? optionsFlow.call_put_ratio.toFixed(2) : "---"}
              </span>
            </div>
            <div className="m-metric">
              <span className="m-metric__label">Call Premium</span>
              <span className="m-metric__value m-metric__value--primary">
                {optionsFlow.call_premium != null ? `$${formatFlowNumber(optionsFlow.call_premium)}` : "---"}
              </span>
            </div>
            <div className="m-metric">
              <span className="m-metric__label">Put Premium</span>
              <span className="m-metric__value m-metric__value--primary">
                {optionsFlow.put_premium != null ? `$${formatFlowNumber(optionsFlow.put_premium)}` : "---"}
              </span>
            </div>
          </div>
        )}

        {section === "history" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {daily.length === 0 ? (
              <div className="alert-item" style={{ textAlign: "center", padding: "24px 0" }}>
                No history available
              </div>
            ) : (
              daily.map((d) => {
                const pct = typeof d.dp_buy_ratio === "number" ? Math.round(d.dp_buy_ratio * 100) : null;
                const dirTone =
                  d.flow_direction === "ACCUMULATION" ? "pos" as const
                    : d.flow_direction === "DISTRIBUTION" ? "neg" as const
                      : "mut" as const;
                return (
                  <div
                    key={d.date}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "80px 1fr auto auto",
                      gap: 8,
                      alignItems: "center",
                      padding: "8px 0",
                      borderBottom: "1px solid var(--line-grid)",
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-muted)",
                      }}
                    >
                      {d.date}
                    </span>
                    <span
                      className={`m-pill m-pill--${dirTone}`}
                      style={{ minHeight: 24, fontSize: 10, padding: "2px 8px" }}
                    >
                      {(d.flow_direction ?? "NEUTRAL").replace("_", " ")}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-muted)",
                      }}
                    >
                      {d.flow_strength ?? "--"}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontVariantNumeric: "tabular-nums",
                        color: "var(--text-primary)",
                      }}
                    >
                      {pct != null ? `${pct}%` : "--"}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>

      {data?.fetched_at && (
        <div
          className="report-meta"
          style={{ padding: "0 16px 12px", margin: 0, fontSize: 10 }}
        >
          {new Date(data.fetched_at).toLocaleString()}
          {isAnalyzing ? " · Refreshing..." : ""}
        </div>
      )}
    </div>
  );
}

function deriveVerdict(data: FlowReportData | null): Verdict | null {
  if (!data) return null;
  // Prefer the server-derived verdict when present (kept in sync with
  // classifyFlowSignal). Re-run client-side as a defensive fallback.
  if (data.verdict?.direction) {
    const verdict = classifyFlowSignal({
      dark_pool: data.dark_pool,
      options_flow: data.options_flow,
      combined_signal: data.combined_signal,
      analysis: data.analysis,
    });
    return {
      ...verdict,
      direction: data.verdict.direction,
      confidence: data.verdict.confidence ?? verdict.confidence,
    };
  }
  return classifyFlowSignal({
    dark_pool: data.dark_pool,
    options_flow: data.options_flow,
    combined_signal: data.combined_signal,
    analysis: data.analysis,
  });
}

function directionMeta(direction: FlowDirection | null) {
  if (direction === "BULLISH") {
    return { label: "Bullish", className: "bullish", Icon: ArrowUpRight };
  }
  if (direction === "BEARISH") {
    return { label: "Bearish", className: "bearish", Icon: ArrowDownRight };
  }
  return { label: "Neutral", className: "neutral", Icon: Minus };
}

function SignalBadge({
  ticker,
  verdict,
  status,
  onRefresh,
}: {
  ticker: string;
  verdict: Verdict | null;
  status: ReturnType<typeof useTickerFlowReport>["status"];
  onRefresh: () => void;
}) {
  const direction = verdict?.direction ?? null;
  const meta = directionMeta(direction);
  const Icon = meta.Icon;
  const showVerdict = status === "fresh" || status === "error" || status === "scanning";

  return (
    <section className="section ticker-flow-hero">
      <div className="ticker-flow-hero-header">
        <div>
          <div className="ticker-flow-hero-eyebrow">FLOW REPORT</div>
          <div className="ticker-flow-hero-symbol">{ticker}</div>
        </div>
        <button
          type="button"
          className="ticker-flow-refresh"
          onClick={onRefresh}
          disabled={status === "loading" || status === "scanning"}
          aria-label="Refresh flow report"
        >
          <RefreshCw size={12} />
          <span>{status === "scanning" ? "Analyzing" : "Refresh"}</span>
        </button>
      </div>

      <div
        className={`ticker-flow-badge ticker-flow-badge-${meta.className}`}
        role="status"
        aria-live="polite"
        data-direction={direction ?? "PENDING"}
        data-status={status}
      >
        <div className="ticker-flow-badge-icon">
          {showVerdict && verdict ? <Icon size={28} /> : <PulseDot />}
        </div>
        <div className="ticker-flow-badge-body">
          <div className="ticker-flow-badge-label">
            {showVerdict && verdict ? meta.label : `Analyzing ${ticker}`}
          </div>
          <div className="ticker-flow-badge-rationale">
            {showVerdict && verdict
              ? verdict.rationale
              : "Reconstructing dark pool and options flow"}
          </div>
        </div>
        {showVerdict && verdict && (
          <div className="ticker-flow-badge-conviction">
            <div className="ticker-flow-badge-conviction-value">{verdict.confidence}</div>
            <div className="ticker-flow-badge-conviction-label">Conviction</div>
          </div>
        )}
      </div>
    </section>
  );
}

function PulseDot() {
  return <span className="ticker-flow-pulse" aria-hidden="true" />;
}

function AnalyzingPanel({ ticker, status }: { ticker: string; status: string }) {
  const message =
    status === "scanning"
      ? `Analyzing ${ticker}`
      : `Loading cached report for ${ticker}`;
  return (
    <section className="section">
      <div className="section-body">
        <div className="ticker-flow-analyzing">
          <div className="ticker-flow-analyzing-header">
            <div className="ticker-flow-analyzing-spinner" aria-hidden="true" />
            <div className="ticker-flow-analyzing-title">{message}</div>
          </div>
          <ul className="ticker-flow-analyzing-steps">
            <li>Pulling dark pool prints across the last 5 trading sessions</li>
            <li>Reconstructing buy / sell pressure from NBBO mid-cross</li>
            <li>Aggregating institutional options flow</li>
            <li>Synthesizing directional verdict</li>
          </ul>
        </div>
      </div>
    </section>
  );
}

function ReportSections({
  data,
  isAnalyzing,
}: {
  data: FlowReportData;
  isAnalyzing: boolean;
}) {
  const dpAgg = data.dark_pool?.aggregate ?? {};
  const buyRatio = dpAgg.dp_buy_ratio;
  const buyPct = typeof buyRatio === "number" ? Math.round(buyRatio * 100) : null;
  const sellPct = typeof buyRatio === "number" ? 100 - Math.round(buyRatio * 100) : null;
  const optionsFlow = data.options_flow ?? {};
  const dailyAll = data.dark_pool?.daily ?? [];
  const daily = dailyAll.slice().sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));

  return (
    <>
      <section className="section">
        <div className="section-header">
          <div className="section-title">Dark Pool Aggregate</div>
          <div className="report-meta" style={{ margin: 0 }}>
            {data.market_status ?? ""}
          </div>
        </div>
        <div className="section-body ticker-flow-grid">
          <Metric
            label="Direction"
            value={(dpAgg.flow_direction ?? "UNKNOWN").replace("_", " ")}
            tone={dpAgg.flow_direction === "ACCUMULATION" ? "positive" : dpAgg.flow_direction === "DISTRIBUTION" ? "negative" : "neutral"}
          />
          <Metric
            label="Strength"
            value={typeof dpAgg.flow_strength === "number" ? `${dpAgg.flow_strength}` : "--"}
          />
          <Metric
            label="Buy / Sell %"
            value={buyPct == null ? "--" : `${buyPct}% / ${sellPct}%`}
          />
          <Metric
            label="Prints"
            value={dpAgg.num_prints != null ? dpAgg.num_prints.toLocaleString() : "--"}
          />
          <Metric
            label="Total Volume"
            value={dpAgg.total_volume != null ? formatNumber(dpAgg.total_volume) : "--"}
          />
          <Metric
            label="Total Premium"
            value={dpAgg.total_premium != null ? `$${formatNumber(dpAgg.total_premium)}` : "--"}
          />
        </div>
      </section>

      <section className="section">
        <div className="section-header">
          <div className="section-title">Options Flow Bias</div>
        </div>
        <div className="section-body ticker-flow-grid">
          <Metric
            label="Bias"
            value={(optionsFlow.bias ?? "NO_DATA").replace("_", " ")}
            tone={optionsBiasTone(optionsFlow.bias)}
          />
          <Metric
            label="Call/Put Ratio"
            value={optionsFlow.call_put_ratio == null ? "--" : optionsFlow.call_put_ratio.toFixed(2)}
          />
          <Metric
            label="Call Premium"
            value={optionsFlow.call_premium != null ? `$${formatNumber(optionsFlow.call_premium)}` : "--"}
          />
          <Metric
            label="Put Premium"
            value={optionsFlow.put_premium != null ? `$${formatNumber(optionsFlow.put_premium)}` : "--"}
          />
        </div>
      </section>

      {daily.length > 0 && (
        <section className="section">
          <div className="section-header">
            <div className="section-title">Daily Dark Pool History</div>
            <span className="pill neutral">{daily.length} SESSIONS</span>
          </div>
          <div className="section-body">
            <table className="ticker-flow-daily">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Direction</th>
                  <th>Strength</th>
                  <th>Buy %</th>
                  <th>Prints</th>
                </tr>
              </thead>
              <tbody>
                {daily.map((d) => {
                  const pct = typeof d.dp_buy_ratio === "number" ? Math.round(d.dp_buy_ratio * 100) : null;
                  const dirClass =
                    d.flow_direction === "ACCUMULATION"
                      ? "accum"
                      : d.flow_direction === "DISTRIBUTION"
                        ? "distrib"
                        : "neutral";
                  return (
                    <tr key={d.date}>
                      <td className="mono">{d.date}</td>
                      <td>
                        <span className={`pill ${dirClass}`}>
                          {(d.flow_direction ?? "NEUTRAL").replace("_", " ")}
                        </span>
                      </td>
                      <td className="mono">{d.flow_strength ?? "--"}</td>
                      <td className="mono">{pct == null ? "--" : `${pct}%`}</td>
                      <td className="mono">{d.num_prints ?? "--"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="section">
        <div className="report-meta">
          {data.fetched_at
            ? `Report Generated: ${new Date(data.fetched_at).toLocaleString()} - Source: UW API - Dark Pool Lookback: ${data.lookback_days ?? 5} Trading Days`
            : "No report timestamp available"}
          {isAnalyzing ? " - Refreshing in background..." : ""}
        </div>
      </section>
    </>
  );
}

function optionsBiasTone(bias?: string | null): "positive" | "negative" | "neutral" {
  if (!bias) return "neutral";
  if (bias.includes("BULLISH")) return "positive";
  if (bias.includes("BEARISH")) return "negative";
  return "neutral";
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <div className="ticker-flow-metric">
      <div className="ticker-flow-metric-label">{label}</div>
      <div className={`ticker-flow-metric-value tone-${tone}`}>{value}</div>
    </div>
  );
}

function formatNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toLocaleString();
}
