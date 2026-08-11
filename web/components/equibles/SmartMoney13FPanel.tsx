"use client";

import { Landmark } from "lucide-react";
import SectionEmptyState from "../SectionEmptyState";
import SpectralLoader from "../SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "../RegimeStrip";
import {
  changeTone,
  formatCount,
  formatDelta,
  formatPct,
  formatShares,
  formatUsdCompact,
  stalenessTone,
  vintageHeadline,
  type Holder,
  type SuperInvestorPosition,
  type Tone,
} from "./smartMoney13F";
import { useSmartMoney13F } from "./useSmartMoney13F";

/**
 * SmartMoney13FPanel — quarterly institutional positioning depth.
 *
 * Position-level 13F data that Unusual Whales does not publish: who holds the
 * name, at what size, what changed last quarter, and which curated super
 * investors are in it.
 *
 * ⚠ FRAMED AS CONTEXT, NEVER AS A TRIGGER. Every reading here is at least a
 * quarter old and up to 45 days behind its own quarter end, so it fails Gate 2
 * on its own. The vintage banner leads the panel and every freshness string is
 * derived from the payload's `disclosure`, never hardcoded.
 */

const TONE_COLORS: Record<Tone, string> = {
  pos: "var(--positive)",
  neg: "var(--negative)",
  ok: "var(--signal-core)",
  warn: "var(--warn-text)",
  mut: "var(--text-muted)",
};

const MONO_META: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "9px",
  color: "var(--text-muted)",
};

function ToneText({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <span style={{ color: TONE_COLORS[tone] }}>{children}</span>;
}

function HolderRow({ holder, index }: { holder: Holder; index: number }) {
  return (
    <div
      data-testid={`smart-money-13f-holder-${index}`}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 2fr) repeat(3, minmax(0, 1fr))",
        gap: "8px",
        padding: "6px 8px",
        borderBottom: "1px solid color-mix(in srgb, var(--text-muted) 18%, transparent)",
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
      }}
    >
      <span style={{ color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis" }}>
        {holder.institution}
      </span>
      <span style={{ textAlign: "right" }}>{formatUsdCompact(holder.value_usd)}</span>
      <span style={{ textAlign: "right" }}>{formatShares(holder.shares)}</span>
      <span style={{ textAlign: "right" }}>
        <ToneText tone={changeTone(holder.change_type)}>{holder.change_type ?? "---"}</ToneText>
      </span>
    </div>
  );
}

function SuperInvestorRow({
  position,
  index,
}: {
  position: SuperInvestorPosition;
  index: number;
}) {
  return (
    <div
      data-testid={`smart-money-13f-super-${index}`}
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "8px",
        padding: "6px 8px",
        borderBottom: "1px solid color-mix(in srgb, var(--text-muted) 18%, transparent)",
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
      }}
    >
      <span style={{ color: "var(--text-primary)" }}>{position.investor}</span>
      <span style={{ color: "var(--text-secondary)" }}>{position.institution}</span>
      <span>{formatUsdCompact(position.value_usd)}</span>
      <ToneText tone={changeTone(position.change_type)}>{position.change_type ?? "---"}</ToneText>
    </div>
  );
}

export default function SmartMoney13FPanel({ ticker }: { ticker: string }) {
  const { data, loading } = useSmartMoney13F(ticker);

  if (loading && !data) {
    return <SpectralLoader label="Loading 13F institutional positioning" />;
  }

  if (!data || data.missing || !data.headline) {
    return (
      <SectionEmptyState
        icon={Landmark}
        headline="No 13F positioning yet"
        secondary="Institutional holdings appear after the next quarterly filing window is ingested for this ticker."
      />
    );
  }

  const { disclosure, headline, market_context: market } = data;
  const topHolders = headline.top_holders ?? [];
  const superInvestors = headline.super_investors ?? [];

  return (
    <>
      {/* ── Vintage banner: the honesty leads, the numbers follow ── */}
      <div
        className="section"
        data-testid="smart-money-13f-vintage"
        style={{
          borderLeft: `2px solid ${TONE_COLORS[stalenessTone(disclosure?.staleness)]}`,
          borderRadius: "4px",
        }}
      >
        <div className="section-header">
          <div className="section-title">
            <Landmark size={14} />
            13F Positioning Context
          </div>
          <ToneText tone={stalenessTone(disclosure?.staleness)}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "10px" }}>
              {vintageHeadline(disclosure)}
            </span>
          </ToneText>
        </div>
        <div
          data-testid="smart-money-13f-gate-note"
          style={{ ...MONO_META, padding: "0 8px 8px", lineHeight: 1.5 }}
        >
          CONTEXT ONLY, NOT A TRIGGER. {disclosure?.gate_note}
        </div>
      </div>

      {/* ── Headline metrics ──────────────────────────────── */}
      <div className="section">
        <RegimeStrip>
          <RegimeStripCell
            testId="smart-money-13f-filers"
            label="FILERS"
            value={formatCount(headline.filer_count)}
            sub={<>INSTITUTIONS REPORTING</>}
          />
          <RegimeStripCell
            testId="smart-money-13f-filer-delta"
            label="FILERS QOQ"
            value={
              <ToneText tone={(headline.filer_count_delta ?? 0) >= 0 ? "pos" : "neg"}>
                {formatDelta(headline.filer_count_delta)}
              </ToneText>
            }
            sub={<>VS PRIOR QUARTER</>}
          />
          <RegimeStripCell
            testId="smart-money-13f-new"
            label="NEW POSITIONS"
            value={formatCount(headline.new_positions)}
            sub={<>OPENED LAST QUARTER</>}
          />
          <RegimeStripCell
            testId="smart-money-13f-exited"
            label="EXITED"
            value={formatCount(headline.exited_positions)}
            sub={<>CLOSED LAST QUARTER</>}
          />
          <RegimeStripCell
            testId="smart-money-13f-ownership"
            label="OWNERSHIP"
            value={formatPct(headline.ownership_pct)}
            sub={<>OF SHARES OUTSTANDING</>}
          />
          <RegimeStripCell
            testId="smart-money-13f-most-held"
            label="FILER GROWTH RANK"
            value={market?.most_held_rank != null ? `#${market.most_held_rank}` : "---"}
            sub={<>MARKET WIDE, BY QOQ FILER DELTA</>}
          />
        </RegimeStrip>
      </div>

      {/* ── Top holders by value ──────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">Top Holders by Value</div>
          <span style={MONO_META}>{formatCount(data.holder_count)} FILED POSITIONS</span>
        </div>
        {topHolders.map((holder, index) => (
          <HolderRow key={`${holder.cik}-${holder.institution}`} holder={holder} index={index} />
        ))}
      </div>

      {/* ── Named super investors ─────────────────────────── */}
      {superInvestors.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">Super Investors Holding</div>
          </div>
          {superInvestors.map((position, index) => (
            <SuperInvestorRow key={`${position.cik}-${position.investor}`} position={position} index={index} />
          ))}
        </div>
      )}
    </>
  );
}
