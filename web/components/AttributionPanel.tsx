"use client";

import { BarChart3, Target, Crosshair, Shield } from "lucide-react";
import { useAttribution } from "@/lib/useAttribution";
import type {
  AttributionData,
  StrategyAttribution,
  EdgeAttribution,
  RiskAttribution,
  TickerAttributionEntry,
} from "@/lib/types";
import { fmtSignedUsd as fmtUsd, fmtPct as fmtPctShared, toneClass } from "@/lib/format";

function fmtPct(value: number | null): string {
  if (value === null) return "---";
  return fmtPctShared(value, 0);
}

function PnlBar({ value, max }: { value: number; max: number }) {
  if (max === 0) return null;
  const pct = Math.min(Math.abs(value) / max * 100, 100);
  const isPositive = value >= 0;
  return (
    <div
      className="attribution-pnl-bar"
      data-testid="attribution-pnl-bar"
    >
      <div
        className={`attribution-pnl-fill ${isPositive ? "positive" : "negative"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function StrategyTable({ strategies, maxPnl }: { strategies: StrategyAttribution[]; maxPnl: number }) {
  return (
    <div className="attribution-table" data-testid="attribution-strategy-table">
      <div className="attribution-table-header">
        <span>Strategy</span>
        <span>Trades</span>
        <span>Win Rate</span>
        <span>P&L</span>
        <span />
      </div>
      {strategies.map((s) => (
        <div key={s.strategy_id} className="attribution-table-row">
          <span className="attribution-strategy-name">{s.strategy_name}</span>
          <span className="mono">{s.closed_count}/{s.trade_count}</span>
          <span className="mono">{fmtPct(s.win_rate)}</span>
          <span className={`mono ${toneClass(s.realized_pnl)}`}>{fmtUsd(s.realized_pnl)}</span>
          <PnlBar value={s.realized_pnl} max={maxPnl} />
        </div>
      ))}
    </div>
  );
}

function EdgeTable({ edges, maxPnl }: { edges: EdgeAttribution[]; maxPnl: number }) {
  const edgeLabels: Record<string, string> = {
    dark_pool: "Dark Pool",
    iv_mispricing: "IV Mispricing",
    thesis: "Thesis",
    garch: "GARCH",
    vcg: "VCG",
    cri: "CRI",
    other_edge: "Other Edge",
    none: "No Edge",
  };
  return (
    <div className="attribution-table" data-testid="attribution-edge-table">
      <div className="attribution-table-header">
        <span>Edge Type</span>
        <span>Trades</span>
        <span>Win Rate</span>
        <span>P&L</span>
        <span />
      </div>
      {edges.map((e) => (
        <div key={e.edge_type} className="attribution-table-row">
          <span>{edgeLabels[e.edge_type] ?? e.edge_type}</span>
          <span className="mono">{e.closed_count}/{e.trade_count}</span>
          <span className="mono">{fmtPct(e.win_rate)}</span>
          <span className={`mono ${toneClass(e.realized_pnl)}`}>{fmtUsd(e.realized_pnl)}</span>
          <PnlBar value={e.realized_pnl} max={maxPnl} />
        </div>
      ))}
    </div>
  );
}

function RiskTable({ risks }: { risks: RiskAttribution[] }) {
  const riskLabels: Record<string, string> = {
    defined: "Defined Risk",
    undefined: "Undefined Risk",
    equity: "Equity",
    unknown: "Unknown",
  };
  return (
    <div className="attribution-table" data-testid="attribution-risk-table">
      <div className="attribution-table-header">
        <span>Risk Profile</span>
        <span>Trades</span>
        <span>Win Rate</span>
        <span>P&L</span>
      </div>
      {risks.map((r) => (
        <div key={r.risk_type} className="attribution-table-row">
          <span>{riskLabels[r.risk_type] ?? r.risk_type}</span>
          <span className="mono">{r.closed_count}/{r.trade_count}</span>
          <span className="mono">{fmtPct(r.win_rate)}</span>
          <span className={`mono ${toneClass(r.realized_pnl)}`}>{fmtUsd(r.realized_pnl)}</span>
        </div>
      ))}
    </div>
  );
}

function TickerLeaderboard({ tickers }: { tickers: TickerAttributionEntry[] }) {
  const top = tickers.slice(0, 5);
  const bottom = [...tickers].sort((a, b) => a.realized_pnl - b.realized_pnl).slice(0, 5);
  return (
    <div className="performance-grid-2" data-testid="attribution-ticker-leaderboard">
      <div>
        <div className="attribution-mini-header positive">Top Performers</div>
        {top.map((t) => (
          <div key={`top-${t.ticker}`} className="attribution-ticker-row">
            <span className="mono">{t.ticker}</span>
            <span className={`mono ${toneClass(t.realized_pnl)}`}>{fmtUsd(t.realized_pnl)}</span>
          </div>
        ))}
      </div>
      <div>
        <div className="attribution-mini-header negative">Bottom Performers</div>
        {bottom.map((t) => (
          <div key={`bot-${t.ticker}`} className="attribution-ticker-row">
            <span className="mono">{t.ticker}</span>
            <span className={`mono ${toneClass(t.realized_pnl)}`}>{fmtUsd(t.realized_pnl)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function KellyCalibration({ data }: { data: AttributionData }) {
  const entries = Object.entries(data.kelly_calibration);
  if (entries.length === 0) return null;

  const strategyNames: Record<string, string> = {};
  for (const s of data.by_strategy) {
    strategyNames[s.strategy_id] = s.strategy_name;
  }

  return (
    <div className="attribution-table" data-testid="attribution-kelly-table">
      <div className="attribution-table-header">
        <span>Strategy</span>
        <span>Expected WR</span>
        <span>Actual WR</span>
        <span>Accuracy</span>
        <span>Samples</span>
      </div>
      {entries.map(([sid, cal]) => (
        <div key={sid} className="attribution-table-row">
          <span>{strategyNames[sid] ?? sid}</span>
          <span className="mono">{fmtPct(cal.expected_win_rate)}</span>
          <span className="mono">{fmtPct(cal.actual_win_rate)}</span>
          <span className={`mono ${cal.accuracy !== null && cal.accuracy >= 0.7 ? "positive" : cal.accuracy !== null && cal.accuracy < 0.3 ? "negative" : ""}`}>
            {fmtPct(cal.accuracy)}
          </span>
          <span className="mono">{cal.sample_size}</span>
        </div>
      ))}
    </div>
  );
}

export default function AttributionPanel() {
  const { data, loading, error } = useAttribution();

  if (loading && !data) {
    return (
      <div className="section" data-testid="attribution-panel">
        <div className="section-header">
          <div className="section-title">
            <BarChart3 size={14} />
            Attribution
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body performance-empty">
          Computing attribution across strategies...
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="section" data-testid="attribution-panel">
        <div className="section-header">
          <div className="section-title">
            <BarChart3 size={14} />
            Attribution
          </div>
          <span className="pill undefined">UNAVAILABLE</span>
        </div>
        <div className="section-body performance-empty">
          {error ?? "Attribution data not available."}
        </div>
      </div>
    );
  }

  const maxStrategyPnl = Math.max(...data.by_strategy.map((s) => Math.abs(s.realized_pnl)), 1);
  const maxEdgePnl = Math.max(...data.by_edge.map((e) => Math.abs(e.realized_pnl)), 1);

  return (
    <div data-testid="attribution-panel">
      {/* Strategy Attribution */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <BarChart3 size={14} />
            Strategy Attribution
          </div>
          <span className="pill defined">{data.closed_trades} CLOSED</span>
        </div>
        <div className="section-body">
          <StrategyTable strategies={data.by_strategy} maxPnl={maxStrategyPnl} />
        </div>
      </div>

      {/* Edge Quality + Risk Profile side by side */}
      <div className="performance-grid-2">
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Crosshair size={14} />
              Edge Quality
            </div>
            <span className="pill neutral">BY SOURCE</span>
          </div>
          <div className="section-body">
            <EdgeTable edges={data.by_edge} maxPnl={maxEdgePnl} />
          </div>
        </div>

        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Shield size={14} />
              Risk Profile
            </div>
            <span className="pill neutral">DEFINED VS UNDEFINED</span>
          </div>
          <div className="section-body">
            <RiskTable risks={data.by_risk} />
          </div>
        </div>
      </div>

      {/* Ticker Leaderboard */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Target size={14} />
            Ticker Attribution
          </div>
          <span className="pill neutral">{data.by_ticker.length} TICKERS</span>
        </div>
        <div className="section-body">
          <TickerLeaderboard tickers={data.by_ticker} />
        </div>
      </div>

      {/* Kelly Calibration */}
      {Object.keys(data.kelly_calibration).length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Target size={14} />
              Kelly Calibration
            </div>
            <span className="pill neutral">PREDICTED VS ACTUAL</span>
          </div>
          <div className="section-body">
            <KellyCalibration data={data} />
          </div>
        </div>
      )}
    </div>
  );
}
