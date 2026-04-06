"use client";

import { useState } from "react";

const glossary: Record<string, string> = {
  COR1M: "1-month implied correlation across S&P 500 sectors",
  VIX: "CBOE Volatility Index — expected 30-day S&P 500 volatility",
  VVIX: "Volatility of VIX — second-order vol stress signal",
  Kelly: "Kelly criterion — optimal position sizing based on edge and win probability",
  "Kelly Cap": "Maximum position size as percentage of bankroll, bounded by Kelly criterion",
  GARCH: "Generalized Autoregressive Conditional Heteroskedasticity — volatility forecasting model",
  GEX: "Gamma Exposure — net dealer gamma positioning across strikes",
  "GEX Flip": "Strike price where dealer gamma shifts from stabilizing to destabilizing",
  CRI: "Crash Risk Index — composite stress signal from VIX, VVIX, correlation, and momentum",
  CTA: "Commodity Trading Advisor — systematic trend-following funds whose deleveraging creates forced selling",
  RVOL: "Realized volatility — actual historical price movement over a lookback window",
  HV20: "20-day historical volatility",
  HV60: "60-day historical volatility",
  "IV Rank": "Current implied volatility percentile relative to the past year",
  OTM: "Out of the money — option strike beyond current price",
  ATM: "At the money — option strike near current price",
  LEAP: "Long-term equity anticipation security — options with 1+ year expiry",
  "R:R": "Risk-to-reward ratio — potential gain relative to potential loss",
  "Net Liq": "Net liquidation value — total portfolio value if all positions closed",
  Sharpe: "Sharpe ratio — risk-adjusted return per unit of total volatility",
  Sortino: "Sortino ratio — risk-adjusted return per unit of downside volatility",
  VaR: "Value at Risk — maximum expected loss at a given confidence level",
  "Max DD": "Maximum drawdown — largest peak-to-trough decline",
  HYG: "iShares High Yield Corporate Bond ETF — credit market proxy",
};

type GlossaryTermProps = {
  term: string;
  children?: React.ReactNode;
};

export function GlossaryTerm({ term, children }: GlossaryTermProps) {
  const definition = glossary[term];
  if (!definition) return <>{children ?? term}</>;

  return (
    <span className="glossary-term group relative">
      <span className="border-b border-dotted border-secondary/40 cursor-help">
        {children ?? term}
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-normal rounded bg-panel-raised border border-grid px-3 py-2 font-sans text-xs leading-5 text-primary opacity-0 shadow-none transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 min-w-[200px] max-w-[280px] text-center"
      >
        {definition}
      </span>
    </span>
  );
}
