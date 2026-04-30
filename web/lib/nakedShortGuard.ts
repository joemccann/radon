/**
 * Naked short guard — ⚠️ DISABLED 2026-04-30.
 *
 * Both public entry points (`checkNakedShortRisk`, `auditOpenOrders`) return
 * "no risk" / "no violations" unconditionally. The original detection logic
 * is preserved verbatim in private `_checkNakedShortRiskImpl` and
 * `_auditOpenOrdersImpl` for an in-place re-enable.
 *
 * Re-enable: see "Naked Short Protection (Gate 4) → How to re-enable" in
 * `CLAUDE.md` (6-step procedure covering this file, the Python audit, the
 * two test suites, and the CLAUDE.md status block).
 */

/* ---------- types ---------- */

export type OrderPayload = {
  type: "stock" | "option" | "combo";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  expiry?: string;
  strike?: number;
  right?: "C" | "P";
  legs?: {
    expiry: string;
    strike: number;
    right: "C" | "P";
    action: "BUY" | "SELL";
    ratio: number;
  }[];
};

export type NakedShortPortfolioPosition = {
  ticker: string;
  structure_type: string;
  contracts: number;
  direction: string;
  expiry?: string | null;
  legs: {
    direction: "LONG" | "SHORT";
    type: "Call" | "Put" | "Stock";
    contracts: number;
    strike: number | null;
  }[];
};

export type NakedShortPortfolio = {
  positions: NakedShortPortfolioPosition[];
};

export type NakedShortOpenOrder = {
  orderId: number;
  permId: number;
  symbol: string;
  action: string;
  totalQuantity: number;
  contract: {
    secType: string;
    right: string | null;
    strike: number | null;
    expiry: string | null;
    symbol: string;
  };
};

export type GuardResult = { allowed: boolean; reason?: string };

/* ---------- helpers ---------- */

/** Count long stock shares for a ticker across all positions. */
function countLongShares(ticker: string, portfolio: NakedShortPortfolio): number {
  let shares = 0;
  for (const pos of portfolio.positions) {
    if (pos.ticker !== ticker) continue;
    for (const leg of pos.legs) {
      if (leg.type === "Stock" && leg.direction === "LONG") {
        shares += leg.contracts;
      }
    }
  }
  return shares;
}

/** Count existing short call contracts for a ticker across all positions. */
function countExistingShortCalls(ticker: string, portfolio: NakedShortPortfolio): number {
  let contracts = 0;
  for (const pos of portfolio.positions) {
    if (pos.ticker !== ticker) continue;
    for (const leg of pos.legs) {
      if (leg.type === "Call" && leg.direction === "SHORT") {
        contracts += leg.contracts;
      }
    }
  }
  return contracts;
}

function normalizeExpiry(expiry: string | null | undefined): string | null {
  if (!expiry) return null;
  const clean = expiry.replace(/-/g, "");
  return clean.length === 8 ? clean : null;
}

/** Count long call contracts at the same expiry (any strike) that can form a vertical spread. */
function countLongCallsAtExpiry(
  ticker: string,
  expiry: string | null | undefined,
  portfolio: NakedShortPortfolio,
): number {
  const normalizedExpiry = normalizeExpiry(expiry);
  if (!normalizedExpiry) return 0;

  let contracts = 0;
  for (const pos of portfolio.positions) {
    if (pos.ticker !== ticker) continue;
    if (normalizeExpiry(pos.expiry) !== normalizedExpiry) continue;
    for (const leg of pos.legs) {
      if (leg.direction === "LONG" && leg.type === "Call") {
        contracts += leg.contracts;
      }
    }
  }
  return contracts;
}

/** Count long option contracts that match the exact option being sold to close. */
function countMatchingLongOptionContracts(
  ticker: string,
  expiry: string | null | undefined,
  strike: number | null | undefined,
  right: "C" | "P" | null | undefined,
  portfolio: NakedShortPortfolio,
): number {
  const normalizedExpiry = normalizeExpiry(expiry);
  if (!normalizedExpiry || strike == null || !right) return 0;

  const expectedType = right === "C" ? "Call" : "Put";
  let contracts = 0;

  for (const pos of portfolio.positions) {
    if (pos.ticker !== ticker) continue;
    if (normalizeExpiry(pos.expiry) !== normalizedExpiry) continue;
    for (const leg of pos.legs) {
      if (
        leg.direction === "LONG" &&
        leg.type === expectedType &&
        leg.strike === strike
      ) {
        contracts += leg.contracts;
      }
    }
  }

  return contracts;
}

/* ---------- main guard ---------- */

export function checkNakedShortRisk(
  _order: OrderPayload,
  _portfolio: NakedShortPortfolio,
): GuardResult {
  // GUARD DISABLED — always allow.
  return { allowed: true };
}

function _checkNakedShortRiskImpl(
  order: OrderPayload,
  portfolio: NakedShortPortfolio,
): GuardResult {
  // Combo (BAG) orders — inspect leg structure before the BUY early-return.
  // IB BAG orders always carry action=BUY on the envelope; leg actions define the structure.
  if (order.type === "combo" && order.legs && order.legs.length >= 2) {
    // Closing a combo (action=SELL) always reduces exposure → allow.
    if (order.action === "SELL") {
      return { allowed: true };
    }

    // Opening: each SELL call leg must be offset by a BUY call leg (vertical spread)
    // or covered by sufficient long stock. Short put legs are cash-secured → always ok.
    const sellCallRatio = order.legs
      .filter((l) => l.action === "SELL" && l.right === "C")
      .reduce((sum, l) => sum + l.ratio, 0);
    const buyCallRatio = order.legs
      .filter((l) => l.action === "BUY" && l.right === "C")
      .reduce((sum, l) => sum + l.ratio, 0);

    // Uncovered = short call ratio not offset by a long call in this combo
    const uncoveredRatio = sellCallRatio - buyCallRatio;

    if (uncoveredRatio > 0) {
      const shares = countLongShares(order.symbol, portfolio);
      const existingShortCalls = countExistingShortCalls(order.symbol, portfolio);
      const totalShortCalls = existingShortCalls + uncoveredRatio * order.quantity;
      const coveredContracts = Math.floor(shares / 100);

      if (totalShortCalls > coveredContracts) {
        return {
          allowed: false,
          reason: `Naked short call in combo: SELL call leg not covered by long call for ${order.symbol} — add a long call leg or hold ${uncoveredRatio * order.quantity * 100} shares`,
        };
      }
    }

    return { allowed: true };
  }

  // Single-leg: BUY anything → always allowed
  if (order.action === "BUY") {
    return { allowed: true };
  }

  const sym = order.symbol;

  // SELL stock
  if (order.type === "stock") {
    const held = countLongShares(sym, portfolio);
    if (held === 0) {
      return {
        allowed: false,
        reason: `Naked short stock: no long shares held for ${sym}`,
      };
    }
    if (order.quantity > held) {
      return {
        allowed: false,
        reason: `Naked short stock: selling ${order.quantity} shares but only ${held} held for ${sym}`,
      };
    }
    return { allowed: true };
  }

  // SELL option
  if (order.type === "option") {
    // SELL put → cash-secured, always allowed
    if (order.right === "P") {
      return { allowed: true };
    }

    // SELL call → need long calls (vertical spread) or long shares to cover
    if (order.right === "C") {
      // 1. Check if selling to close exact same option (same strike + expiry)
      const closingLongCalls = countMatchingLongOptionContracts(
        sym,
        order.expiry,
        order.strike ?? null,
        order.right,
        portfolio,
      );
      const remainingAfterClose = Math.max(order.quantity - closingLongCalls, 0);
      if (remainingAfterClose === 0) {
        return { allowed: true };
      }

      // 2. Check if long calls at same expiry (any strike) form a vertical spread
      const longCallsAtExpiry = countLongCallsAtExpiry(sym, order.expiry, portfolio);
      // Subtract exact-strike matches already counted (avoid double-counting)
      const spreadCoverage = Math.max(longCallsAtExpiry - closingLongCalls, 0);
      const remainingAfterSpread = Math.max(remainingAfterClose - spreadCoverage, 0);
      if (remainingAfterSpread === 0) {
        return { allowed: true };
      }

      // 3. Fall back to stock coverage for remaining uncovered calls
      const shares = countLongShares(sym, portfolio);
      if (shares === 0 && spreadCoverage === 0) {
        return {
          allowed: false,
          reason: closingLongCalls > 0
            ? `Naked short call: selling ${order.quantity} calls closes ${closingLongCalls} long contracts but leaves ${remainingAfterSpread} uncovered for ${sym}`
            : `Naked short call: no long shares held to cover ${sym} calls`,
        };
      }

      const existingShortCalls = countExistingShortCalls(sym, portfolio);
      const totalShortContracts = existingShortCalls + remainingAfterSpread;
      const coveredContracts = Math.floor(shares / 100);

      if (totalShortContracts > coveredContracts) {
        return {
          allowed: false,
          reason: spreadCoverage > 0
            ? `Naked short call: selling ${order.quantity} ${sym} calls but only ${spreadCoverage} covered by long calls and ${coveredContracts} by shares`
            : `Short a tail: selling ${order.quantity} calls but only ${shares} shares cover ${coveredContracts} contracts for ${sym}`,
        };
      }
      return { allowed: true };
    }
  }

  return { allowed: true };
}

/* ---------- audit open orders ---------- */

export function auditOpenOrders(
  _orders: NakedShortOpenOrder[],
  _portfolio: NakedShortPortfolio,
): { orderId: number; permId: number; reason: string }[] {
  // GUARD DISABLED — always report no violations.
  return [];
}

function _auditOpenOrdersImpl(
  orders: NakedShortOpenOrder[],
  portfolio: NakedShortPortfolio,
): { orderId: number; permId: number; reason: string }[] {
  const violations: { orderId: number; permId: number; reason: string }[] = [];

  for (const order of orders) {
    // Only audit SELL call orders
    if (order.action !== "SELL") continue;
    if (order.contract.secType !== "OPT" || order.contract.right !== "C") continue;

    const sym = order.symbol;
    const closingLongCalls = countMatchingLongOptionContracts(
      sym,
      order.contract.expiry,
      order.contract.strike,
      order.contract.right === "C" || order.contract.right === "P" ? order.contract.right : null,
      portfolio,
    );
    const remainingShortContracts = Math.max(order.totalQuantity - closingLongCalls, 0);
    if (remainingShortContracts === 0) continue;

    const shares = countLongShares(sym, portfolio);

    if (shares === 0) {
      violations.push({
        orderId: order.orderId,
        permId: order.permId,
        reason: closingLongCalls > 0
          ? `Naked short call: open SELL closes ${closingLongCalls} long contracts but leaves ${remainingShortContracts} uncovered for ${sym}`
          : `Naked short call: no long shares held to cover ${sym} calls`,
      });
      continue;
    }

    const existingShortCalls = countExistingShortCalls(sym, portfolio);
    const coveredContracts = Math.floor(shares / 100);

    if (existingShortCalls + remainingShortContracts > coveredContracts) {
      violations.push({
        orderId: order.orderId,
        permId: order.permId,
        reason: closingLongCalls > 0
          ? `Short a tail: open SELL closes ${closingLongCalls} long contracts but still leaves ${remainingShortContracts} uncovered calls for ${sym}; ${shares} shares cover ${coveredContracts} contracts (${existingShortCalls} already short)`
          : `Short a tail: selling ${order.totalQuantity} calls but only ${shares} shares cover ${coveredContracts} contracts for ${sym} (${existingShortCalls} already short)`,
      });
    }
  }

  return violations;
}
