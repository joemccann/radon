/** Operator-facing wording for intake reason codes (scripts/research/intake.py, triage.py). Unknown codes fall back to a readable form. */
const LABELS: Record<string, string> = {
  NUMBER_NOT_ON_PAGE: "Number not on the cited page",
  VERIFY_FAILED: "Verification failed",
  INVALID_CANDIDATE: "Draft failed validation",
  TEXT_ONLY_WITH_FIGURES: "Text-only draft while the cited pages have charts",
  NO_CANDIDATES: "Nothing selected",
  DUPLICATE_OF_PUBLISHED: "Duplicate of a published document",
  SERIES_DENYLIST: "Series is on the denylist",
  SINGLE_STOCK_NOT_IN_BOOK: "Single-name equity research outside the watchlist and portfolio",
  DOC_TYPE_FX_PAIR_NOTE: "Document type: FX pair note",
  DOC_TYPE_CALENDAR: "Document type: calendar",
  HELD_EXPIRED: "Hold expired",
};

export function reasonCodeLabel(code: string): string {
  if (LABELS[code]) return LABELS[code];
  const words = code.toLowerCase().split("_").filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "Unknown reason";
}
