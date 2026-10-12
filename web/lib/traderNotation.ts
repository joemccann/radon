import type { PortfolioLeg } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-12" or an ISO timestamp -> "Oct 12". Date-only strings are read as calendar dates. */
export function formatShortDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dateOnly) {
    const month = Number(dateOnly[2]);
    const day = Number(dateOnly[3]);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    return `${MONTHS[month - 1]} ${day}`;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return `${MONTHS[parsed.getUTCMonth()]} ${parsed.getUTCDate()}`;
}

function strikeText(strike: number): string {
  return Number.isInteger(strike) ? String(strike) : String(Number(strike.toFixed(2)));
}

const JOURNAL_OPTION = /^(Long|Short|Closed) (Call|Put) \$(\d+(?:\.\d+)?) (\d{4}-\d{2}-\d{2})$/;

/** "Short Call $270 2026-10-30" -> "-270C Oct 30". Anything else passes through unchanged. */
export function journalStructureNotation(structure: string): string {
  const m = JOURNAL_OPTION.exec(structure.trim());
  if (!m) return structure;
  const date = formatShortDate(m[4]);
  if (!date) return structure;
  const body = `${strikeText(Number(m[3]))}${m[2] === "Call" ? "C" : "P"} ${date}`;
  if (m[1] === "Short") return `-${body}`;
  if (m[1] === "Closed") return `close ${body}`;
  return body;
}

/** Single-option holding -> "300C" / "-20P"; any other shape keeps its structure label. */
export function positionHoldingNotation(position: { structure: string; legs: PortfolioLeg[] }): string {
  const legs = position.legs ?? [];
  if (legs.length === 1) {
    const leg = legs[0];
    if ((leg.type === "Call" || leg.type === "Put") && leg.strike != null) {
      const body = `${strikeText(leg.strike)}${leg.type === "Call" ? "C" : "P"}`;
      return leg.direction === "SHORT" ? `-${body}` : body;
    }
  }
  return position.structure;
}
