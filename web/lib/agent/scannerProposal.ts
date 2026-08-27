/**
 * Scanner proposal — promotes the top-ranked theta candidate to a ProposalCard.
 *
 * "Actionable" is not a presentation choice: the scanner already emits a
 * verdict and a gate map per candidate. A row is proposal-worthy only when the
 * engine called it THETA_HARVEST *and* every gate passed. Anything else stays
 * in the table, which is why this returns null far more often than not.
 *
 * Confidence is the scanner's own composite score on its native 0-100 scale,
 * normalised to 0-1. Deliberately NOT rescaled against the visible result set:
 * a relative scale would pin the top row at 1.00 on every scan, however weak
 * the field.
 */

import type { ProposalAlternative } from "@/components/agent";
import { thetaStructLabel } from "@/lib/scannerHero";
import type { ThetaHarvesterResult } from "@/lib/types";

export type ScannerProposal = {
  ticker: string;
  statement: string;
  confidence: number;
  alternatives: ProposalAlternative[];
};

const ACTIONABLE_VERDICT = "THETA_HARVEST";
const MAX_ALTERNATIVES = 3;

function allGatesPass(result: ThetaHarvesterResult): boolean {
  const gates = result.gates ?? {};
  const values = Object.values(gates);
  if (!values.length) return false;
  return values.every(Boolean);
}

/** A candidate the engine itself rated actionable. */
export function isActionable(result: ThetaHarvesterResult): boolean {
  return result.verdict === ACTIONABLE_VERDICT && allGatesPass(result) && !result.errors?.length;
}

function isEngineSetupToken(setup: string): boolean {
  return /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$/.test(setup);
}

function structureStatement(result: ThetaHarvesterResult): string {
  return `${result.ticker} ${thetaStructLabel(result)}`;
}

function statementFor(result: ThetaHarvesterResult): string {
  const setup = result.setup?.trim();
  if (setup && !isEngineSetupToken(setup)) return setup;
  if (!setup) {
    const edge = Number.isFinite(result.iv_rv_edge) ? result.iv_rv_edge.toFixed(1) : "---";
    return `${structureStatement(result)}: IV/RV edge ${edge}, range score ${result.range_score}.`;
  }
  return structureStatement(result);
}

function alternativesFrom(rest: ThetaHarvesterResult[]): ProposalAlternative[] {
  return rest.slice(0, MAX_ALTERNATIVES).map((row) => ({
    id: row.ticker,
    label: `${row.ticker} ${thetaStructLabel(row)}`,
    meta: row.verdict === ACTIONABLE_VERDICT ? `SCORE ${Math.round(row.score)}` : row.verdict,
  }));
}

/**
 * Builds the proposal from an ALREADY-SORTED result list (the scanner sorts by
 * the operator's chosen key). Returns null when the leading row is not
 * actionable — the caller renders the table alone.
 */
export function buildScannerProposal(sorted: ThetaHarvesterResult[]): ScannerProposal | null {
  const [top, ...rest] = sorted;
  if (!top || !isActionable(top)) return null;

  const score = Number.isFinite(top.score) ? top.score : 0;
  return {
    ticker: top.ticker,
    statement: statementFor(top),
    confidence: Math.max(0, Math.min(1, score / 100)),
    alternatives: alternativesFrom(rest),
  };
}
