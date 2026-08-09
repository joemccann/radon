"use client";

import { useRegime } from "@/lib/useRegime";
import { useMarkovState } from "@/lib/useMarkovState";
import { useVcg } from "@/lib/useVcg";
import { useGex } from "@/lib/useGex";
import { criRow, engineCount, gexRow, markovRow, vcgRow } from "@/lib/engineState";
import type { MarketState } from "@/lib/useMarketHours";

type Props = {
  marketState: MarketState | null;
};

/**
 * EngineStatePanel — the regime engines that actually run: CRI composite,
 * the Markov band-transition derivation over CRI history, VCG, and GEX.
 * Missing payloads (off-hours, cold caches) read as empty rows, never faults.
 */
export default function EngineStatePanel({ marketState }: Props) {
  const regime = useRegime(marketState);
  const markov = useMarkovState(regime.data?.history);
  const vcg = useVcg(marketState);
  const gex = useGex(marketState);

  const rows = [criRow(regime.data), markovRow(markov), vcgRow(vcg.data), gexRow(gex.data)];
  const withData = engineCount(rows);
  const headerTone = withData === rows.length ? "core" : "warn";

  return (
    <section className="engine-panel snapshot-card">
      <header className="engine-panel__header">
        <div>
          <p className="panel-eyebrow">Structure / 04</p>
          <h3 className="panel-title">Engine state</h3>
        </div>
        <span className={`engine-panel__status engine-panel__status--${headerTone}`}>
          <span className="engine-panel__status-dot" aria-hidden />
          {withData}/{rows.length} reporting
        </span>
      </header>

      <div className="engine-panel__rows">
        {rows.map((row) => (
          <div key={row.name} className={`engine-row engine-row--${row.tone}`}>
            <div className="engine-row__line">
              <span className="engine-row__name">{row.name}</span>
              <span className="engine-row__reading">{row.reading ?? "awaiting data"}</span>
              <span className="engine-row__p">{row.p != null ? `P ${row.p.toFixed(2)}` : "P ---"}</span>
            </div>
            <span className="engine-row__bar">
              {row.p != null ? (
                <span
                  className={`engine-row__bar-fill engine-row__bar-fill--${row.tone}`}
                  style={{ width: `${(row.p * 100).toFixed(0)}%` }}
                />
              ) : null}
            </span>
          </div>
        ))}
      </div>

      <footer className="panel-meta-rail" aria-label="Engine calibration">
        <div className="panel-meta-rail-item">
          <span className="k">basis</span>
          <span className="v">last completed sample per engine</span>
        </div>
      </footer>
    </section>
  );
}
