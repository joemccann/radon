export function normalizeCtaPercentile(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  const normalized = value >= 0 && value < 1 ? value * 100 : value;
  return Math.max(0, Math.min(100, normalized));
}

export function formatCtaPercentileLabel(value: number | null | undefined): string {
  const normalized = normalizeCtaPercentile(value);
  if (normalized == null) return "---";
  const rounded = Math.round(normalized);
  const mod10 = rounded % 10;
  const mod100 = rounded % 100;
  const suffix = mod10 === 1 && mod100 !== 11
    ? "st"
    : mod10 === 2 && mod100 !== 12
      ? "nd"
      : mod10 === 3 && mod100 !== 13
        ? "rd"
        : "th";
  return `${rounded}${suffix}`;
}

// ── Percentile / z-score reconciliation ──────────────────────────────────────
//
// MenthorQ renders the percentile columns as 0-1 fractions on some cards and as
// 0-100 integers on others, and the vision extractor is told to report integers.
// When it obeys on a fractional card it rounds 0.43 to 0 and 0.98 to 1, so a
// max-LONG row lands on the page as "0th pctile" and the verdict flips to MAX
// SHORT. The z-score in the same row is extracted independently and is not
// rounded, which makes it the check: percentile_3m and z_score_3m measure the
// same position against the same 3M window, so they cannot disagree.

const PERCENTILE_FIELDS = ["percentile_1m", "percentile_3m", "percentile_1y"] as const;

// Widest gap seen between a sound percentile and the one its z-score implies,
// across every menthorq_cta payload on record, is ~24 points (thin-tailed
// series like Natural Gas). 35 sits clear of that and still catches every
// rounded row that inverts a narrative.
const Z_DISAGREEMENT_LIMIT = 35;

/** Abramowitz & Stegun 7.1.26 — max error 1.5e-7, ample at percentile scale. */
function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * z);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-z * z);
  return sign * y;
}

/** The percentile a 3M z-score implies, 0-100. */
export function ctaPercentileFromZ(z: number | null | undefined): number | null {
  if (z == null || !Number.isFinite(z)) return null;
  return 50 * (1 + erf(z / Math.SQRT2));
}

type CtaRowLike = Record<string, unknown>;
type CtaTablesLike = Record<string, CtaRowLike[]>;

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Same contract in two tables is the same row: name, position and z all match. */
function rowKey(row: CtaRowLike): string {
  const name = String(row.underlying ?? "").trim().toLowerCase();
  return `${name}|${num(row.position_today)}|${num(row.z_score_3m)}`;
}

/**
 * Scale is a property of the ROW, not of a single cell. A card that renders
 * fractions renders them in every column, so a 1.0 sitting beside a 0.98 is
 * the 100th percentile — reading that cell on its own calls it the 1st and
 * inverts the row.
 */
function normalizedTrio(row: CtaRowLike): (number | null)[] {
  const raw = PERCENTILE_FIELDS.map((field) => num(row[field]));
  const present = raw.filter((v): v is number => v != null);
  const fractional =
    present.length > 0 &&
    present.every((v) => v >= 0 && v <= 1) &&
    present.some((v) => !Number.isInteger(v));
  return raw.map((v) => {
    if (v == null) return null;
    return Math.max(0, Math.min(100, Math.round(fractional ? v * 100 : v)));
  });
}

/**
 * Normalize every percentile to 0-100, repair a row whose percentiles were
 * rounded away by copying the same row from another table, and null out what
 * neither survives: a percentile its own z-score flatly contradicts is not a
 * number to publish.
 */
export function reconcileCtaTables<T extends CtaTablesLike>(tables: T): T;
export function reconcileCtaTables<T extends CtaTablesLike>(tables: T | null | undefined): T | null;
export function reconcileCtaTables(tables: CtaTablesLike | null | undefined): CtaTablesLike | null {
  if (!tables) return null;

  // Best trio per shared row: the one whose 3M percentile its z-score agrees with.
  const best = new Map<string, { trio: (number | null)[]; gap: number }>();
  for (const rows of Object.values(tables)) {
    for (const row of rows ?? []) {
      const trio = normalizedTrio(row);
      const implied = ctaPercentileFromZ(num(row.z_score_3m));
      const gap = implied == null || trio[1] == null ? Infinity : Math.abs(trio[1] - implied);
      const key = rowKey(row);
      const current = best.get(key);
      if (!current || gap < current.gap) best.set(key, { trio, gap });
    }
  }

  const out: CtaTablesLike = {};
  for (const [table, rows] of Object.entries(tables)) {
    out[table] = (rows ?? []).map((row) => {
      const chosen = best.get(rowKey(row));
      const trio = chosen ? chosen.trio : normalizedTrio(row);
      const implied = ctaPercentileFromZ(num(row.z_score_3m));
      const contradicted =
        implied != null && trio[1] != null && Math.abs(trio[1] - implied) > Z_DISAGREEMENT_LIMIT;
      const next: CtaRowLike = { ...row };
      PERCENTILE_FIELDS.forEach((field, i) => {
        next[field] = contradicted ? null : trio[i];
      });
      return next;
    });
  }
  return out;
}
