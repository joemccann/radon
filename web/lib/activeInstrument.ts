/**
 * Instrument-detail modal target for the position tables (desktop and
 * mobile). The owner stores the leg's IDENTITY, re-resolves the leg from the
 * latest positions on every refresh, and keeps the resolved object stable
 * across mark-only refreshes so dependents keyed on it (the ticket's quantity
 * seed) do not reset on every portfolio poll.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";

export type ActiveInstrumentIdentity = {
  positionId: number;
  conId: number | null;
  legIndex: number;
  /** Contract fields, used when the leg carries no broker conId. */
  contract?: Pick<PortfolioLeg, "type" | "strike" | "direction" | "expiry">;
};

export type ResolvedInstrument = { leg: PortfolioLeg; ticker: string; expiry: string };

function sameContract(
  leg: PortfolioLeg,
  contract: NonNullable<ActiveInstrumentIdentity["contract"]>,
): boolean {
  return leg.type === contract.type
    && leg.strike === contract.strike
    && leg.direction === contract.direction
    && (leg.expiry ?? null) === (contract.expiry ?? null);
}

export function resolveActiveInstrument(
  positions: PortfolioPosition[],
  identity: ActiveInstrumentIdentity | null,
): ResolvedInstrument | null {
  if (!identity) return null;
  const position = positions.find((candidate) => candidate.id === identity.positionId);
  if (!position) return null;
  let leg: PortfolioLeg | undefined;
  if (identity.conId != null) {
    leg = position.legs.find((candidate) => candidate.con_id === identity.conId);
  } else if (identity.contract) {
    const contract = identity.contract;
    leg = position.legs.find((candidate) => sameContract(candidate, contract));
  } else {
    leg = position.legs[identity.legIndex];
  }
  return leg ? { leg, ticker: position.ticker, expiry: position.expiry } : null;
}

export function instrumentIdentityFor(
  leg: PortfolioLeg,
  source: PortfolioPosition,
): ActiveInstrumentIdentity {
  return {
    positionId: source.id,
    conId: leg.con_id ?? null,
    legIndex: source.legs.indexOf(leg),
    contract: { type: leg.type, strike: leg.strike, direction: leg.direction, expiry: leg.expiry ?? null },
  };
}

/** Everything but the per-tick marks: a change here is a real position change. */
function positionKey(resolved: ResolvedInstrument): string {
  const { market_price: _mp, market_value: _mv, market_price_is_calculated: _mc, ...rest } = resolved.leg;
  return JSON.stringify([resolved.ticker, resolved.expiry, rest]);
}

export function useActiveInstrument(
  positions: PortfolioPosition[],
  sourceFor?: (position: PortfolioPosition) => PortfolioPosition,
): {
  instrument: ResolvedInstrument | null;
  open: (leg: PortfolioLeg, position: PortfolioPosition) => void;
  close: () => void;
} {
  const [identity, setIdentity] = useState<ActiveInstrumentIdentity | null>(null);
  const stableRef = useRef<{ key: string; value: ResolvedInstrument } | null>(null);

  const open = useCallback((leg: PortfolioLeg, position: PortfolioPosition) => {
    setIdentity(instrumentIdentityFor(leg, sourceFor?.(position) ?? position));
  }, [sourceFor]);
  const close = useCallback(() => setIdentity(null), []);

  const instrument = useMemo(() => {
    const resolved = resolveActiveInstrument(
      sourceFor ? positions.map((position) => sourceFor(position)) : positions,
      identity,
    );
    if (!resolved) {
      stableRef.current = null;
      return null;
    }
    const key = positionKey(resolved);
    if (stableRef.current?.key === key) return stableRef.current.value;
    stableRef.current = { key, value: resolved };
    return resolved;
  }, [identity, positions, sourceFor]);

  // The leg left the portfolio (closed out, re-keyed): drop the modal.
  useEffect(() => {
    if (identity && !instrument) setIdentity(null);
  }, [identity, instrument]);

  return { instrument, open, close };
}
