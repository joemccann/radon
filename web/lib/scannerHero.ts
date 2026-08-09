import type { StrengthConfirmationResult, ThetaHarvesterResult } from "./types";

export type GateCell = {
  code: string;
  /** null = factor absent from the payload (unknown, not failed). */
  passed: boolean | null;
  title: string;
};

export type BadgeTone = "strong" | "warn" | "fault" | "neutral";

/** Display order + codes for the 7-gate ladder, keyed to the scanner's
 *  `factors[].group` strings (strength_confirmation_scanner.py). */
const GATE_ORDER: ReadonlyArray<{ code: string; group: string }> = [
  { code: "Q", group: "Q-SCORES" },
  { code: "GX", group: "NET GEX" },
  { code: "CL", group: "CALL POSITIONING" },
  { code: "TM", group: "TERM STRUCTURE" },
  { code: "SM", group: "VOLATILITY SMILE" },
  { code: "SY", group: "SYSTEMATIC POSITIONING" },
  { code: "BR", group: "MARKET BREADTH" },
];

export const GATE_CODES = GATE_ORDER.map((g) => g.code);

export function thetaStructLabel(result: ThetaHarvesterResult): string {
  const put = result.structure?.short_put?.strike;
  const call = result.structure?.short_call?.strike;
  if (put == null || call == null) return "—";
  return `SHORT ${put}P / ${call}C`;
}

export function gateCells(result: StrengthConfirmationResult): GateCell[] {
  const byGroup = new Map(result.factors.map((f) => [f.group, f]));
  return GATE_ORDER.map(({ code, group }) => {
    const factor = byGroup.get(group);
    return {
      code,
      passed: factor ? factor.passed : null,
      title: `${group} · ${factor ? (factor.passed ? "PASS" : "FAIL") : "NO DATA"}`,
    };
  });
}

export function strengthBadge(verdict: string): { label: string; tone: BadgeTone } {
  switch (verdict) {
    case "REAL_STRENGTH_CONFIRMED":
      return { label: "CONFIRMED", tone: "strong" };
    case "WATCHLIST":
      return { label: "PARTIAL", tone: "warn" };
    case "WEAK":
      return { label: "WEAK", tone: "fault" };
    default:
      return { label: verdict, tone: "neutral" };
  }
}

export function scoreTone(score: number): "strong" | "warn" | "fault" {
  if (score >= 70) return "strong";
  if (score >= 50) return "warn";
  return "fault";
}
