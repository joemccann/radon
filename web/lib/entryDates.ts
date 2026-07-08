/**
 * Per-contract entry-date derivation from journal rows.
 *
 * A position fully closed and gone from the portfolio has no live entry date,
 * and the per-ticker trade_log_dates map carries only its LATEST (close) date.
 * `buildContractEntryDates` recovers the true entry day per option contract by
 * walking that contract's fills in date order and tracking signed net quantity
 * (mirroring scripts/clients/journal_basis.py's opening-fill convention): the
 * answer is the date net quantity LAST departed zero — the start of the current
 * open episode. This is correct even when a contract is opened, closed, and
 * re-opened, which a naive MIN(date) over all fills would get wrong.
 *
 * Journal `filled_at` / `$.date` values are date-only granularity, so the
 * derived entry dates stay date-only.
 */

export type JournalEntryRow = {
  ticker?: string | null;
  expiry?: string | null;
  right?: string | null;
  strike?: number | string | null;
  action?: string | null;
  contracts?: number | null;
  date?: string | null;
};

/**
 * Canonical per-contract key `SYMBOL|EXPIRY|RIGHT|STRIKE`, matching the
 * conId-free form the share card reconstructs from executed-order fills.
 * Returns null when any component is missing or unparseable, so bad rows drop
 * out instead of forming a junk bucket.
 */
export function contractEntryKey(
  ticker: string | null | undefined,
  expiry: string | null | undefined,
  right: string | null | undefined,
  strike: number | string | null | undefined,
): string | null {
  if (ticker == null || expiry == null || right == null || strike == null) return null;

  const symbol = String(ticker).trim().split(/\s+/)[0]?.toUpperCase();
  if (!symbol) return null;

  const expiryCompact = String(expiry).replace(/-/g, "");
  if (!expiryCompact) return null;

  const rightRaw = String(right).trim().toUpperCase();
  const normalizedRight = rightRaw === "C" || rightRaw === "CALL" ? "C"
    : rightRaw === "P" || rightRaw === "PUT" ? "P"
    : null;
  if (!normalizedRight) return null;

  const strikeNum = Number(strike);
  if (!Number.isFinite(strikeNum)) return null;

  return `${symbol}|${expiryCompact}|${normalizedRight}|${strikeNum}`;
}

/** Signed quantity for a fill: opening/closing direction from the action label,
 *  magnitude from the contract count. Null when the action is not a buy/sell or
 *  the count is not a positive number. */
function signedQuantity(action: string | null | undefined, contracts: number | null | undefined): number | null {
  if (typeof contracts !== "number" || !Number.isFinite(contracts) || contracts <= 0) return null;
  if (action == null) return null;
  const label = action.toUpperCase();
  if (label.includes("BUY")) return contracts;
  if (label.includes("SELL")) return -contracts;
  return null;
}

/** Date-only (YYYY-MM-DD) portion of a journal timestamp, or null. */
function dateOnly(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  return value.slice(0, 10);
}

type WalkableFill = { date: string; qty: number };

/**
 * Map of `SYMBOL|EXPIRY|RIGHT|STRIKE` -> the date the current open episode
 * began. Rows missing a key, a date, a signable action, or a positive contract
 * count are skipped.
 */
export function buildContractEntryDates(rows: readonly JournalEntryRow[]): Record<string, string> {
  const byContract = new Map<string, WalkableFill[]>();

  for (const row of rows) {
    const key = contractEntryKey(row.ticker, row.expiry, row.right, row.strike);
    if (!key) continue;
    const date = dateOnly(row.date);
    if (!date) continue;
    const qty = signedQuantity(row.action, row.contracts);
    if (qty == null) continue;

    const fills = byContract.get(key);
    if (fills) fills.push({ date, qty });
    else byContract.set(key, [{ date, qty }]);
  }

  const entryDates: Record<string, string> = {};
  for (const [key, fills] of byContract) {
    fills.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    let net = 0;
    let episodeStart: string | null = null;
    for (const { date, qty } of fills) {
      const prev = net;
      net += qty;
      // A new open episode begins whenever the holding leaves flat or flips
      // through zero to the opposite side. A return exactly to zero is a close,
      // not a start, so it never updates episodeStart.
      if (net !== 0 && Math.sign(prev) !== Math.sign(net)) {
        episodeStart = date;
      }
    }
    if (episodeStart) entryDates[key] = episodeStart;
  }

  return entryDates;
}
