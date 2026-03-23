"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  ClipboardList,
  ArrowDown,
  ArrowUp,
  Loader2,
  Search,
  Sparkles,
  TrendingDown,
  TriangleAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import type { BlotterTrade, DiscoverCandidate, ExecutedOrder, FlowAnalysisPosition, OpenOrder, OrdersData, PortfolioData, PortfolioPosition, ScannerSignal, TradeEntry, WorkspaceSection } from "@/lib/types";
import { useOrderActions } from "@/lib/OrderActionsContext";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { useJournal } from "@/lib/useJournal";
import { useDiscover } from "@/lib/useDiscover";
import { useFlowAnalysis } from "@/lib/useFlowAnalysis";
import { useScanner } from "@/lib/useScanner";
import { useBlotter } from "@/lib/useBlotter";
import { useSort, type SortDirection } from "@/lib/useSort";
import { useTableFilter } from "@/lib/useTableFilter";
import TableSearch from "./TableSearch";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import {
  buildOpenOrderDisplayRows,
  type OpenOrderDisplayRow,
  buildExecutedGroupDescription,
  resolveOpenOrderComboPrice,
} from "@/lib/openOrderCombos";
import { buildGroupedComboModifyTarget } from "@/lib/openOrderComboModify";
import PositionTable from "./PositionTable";
import { TableSkeleton } from "@/components/ui/Skeleton";
import CancelOrderDialog from "./CancelOrderDialog";
import ModifyOrderModal from "./ModifyOrderModal";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import RegimePanel from "./RegimePanel";
import CtaPage from "./CtaPage";
import PerformancePanel from "./PerformancePanel";
import InfoTooltip from "./InfoTooltip";
import SharePnlButton, { type SharePnlData } from "./SharePnlButton";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import TickerLink from "./TickerLink";
import TickerWorkspace from "./TickerWorkspace";
import { MarketState } from "@/lib/useMarketHours";

/* ─── Re-exports for backward compat ──────────────────── */

export {
  fmtUsd,
  fmtPrice,
  fmtPriceOrCalculated,
  resolveMarketValue,
  resolveEntryCost,
  getAvgEntry,
  getMultiplier,
  getLastPriceIsCalculated,
  legPriceKey,
  getOptionDailyChg,
  getLastPrice,
} from "@/lib/positionUtils";

/* ─── Share P&L helpers ────────────────────────────────── */

/** Build a human-readable description from an ExecutedOrder.
 *  e.g. "Long AAOI 2026-04-17 Call $45.00" */
/** Build a human-readable description from an ExecutedOrder.
 *  When realizedPNL is present (closing trade), show the ORIGINAL position
 *  direction: BOT closing = was Short, SLD closing = was Long.
 *  When no realizedPNL (opening trade): BOT = Long, SLD = Short. */
function execOrderDescription(e: ExecutedOrder): string {
  const c = e.contract;
  const isClosing = e.realizedPNL != null;
  const side = e.side === "BOT"
    ? (isClosing ? "Short" : "Long")
    : e.side === "SLD"
      ? (isClosing ? "Long" : "Short")
      : e.side;
  if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
    const right = c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : c.right;
    return `${side} ${c.symbol} ${c.expiry} ${right} $${c.strike.toFixed(2)}`;
  }
  return `${side} ${c.symbol}`;
}

function execOrderShareData(e: ExecutedOrder): SharePnlData {
  return {
    description: execOrderDescription(e),
    pnl: e.realizedPNL ?? 0,
    pnlPct: e.realizedPNL != null && e.avgPrice != null && e.avgPrice > 0
      ? (e.realizedPNL / (e.avgPrice * e.quantity * (e.contract.secType === "OPT" ? 100 : 1))) * 100
      : null,
    commission: e.commission,
    fillPrice: e.avgPrice,
    entryPrice: null,
    exitPrice: null,
    entryTime: null,
    exitTime: null,
    time: e.time ? new Date(e.time).toLocaleString() : "",
  };
}

function executedOptionContractKey(fill: ExecutedOrder): string | null {
  if (fill.contract.secType !== "OPT") return null;
  if (fill.contract.conId != null) return `conid:${fill.contract.conId}`;

  const symbol = fill.contract.symbol?.toUpperCase();
  const expiry = fill.contract.expiry?.replace(/-/g, "");
  const strike = fill.contract.strike;
  const rightRaw = fill.contract.right;
  const right = rightRaw === "CALL" ? "C" : rightRaw === "PUT" ? "P" : rightRaw;

  if (!symbol || !expiry || !right || strike == null) return null;
  return `${symbol}|${expiry}|${right}|${strike}`;
}

function resolveOpeningLegBasis(
  group: PositionFillGroup,
  allGroups?: PositionFillGroup[],
): { entryPrice: number | null; entryNotional: number; entryTime: string | null } {
  if (!allGroups) return { entryPrice: null, entryNotional: 0, entryTime: null };

  const closeOptFills = group.fills.filter((fill) => fill.contract.secType === "OPT");
  if (closeOptFills.length === 0) return { entryPrice: null, entryNotional: 0, entryTime: null };

  const requiredByContract = new Map<string, number>();
  for (const fill of closeOptFills) {
    const key = executedOptionContractKey(fill);
    if (!key) continue;
    requiredByContract.set(key, (requiredByContract.get(key) ?? 0) + Math.abs(fill.quantity));
  }
  if (requiredByContract.size === 0) return { entryPrice: null, entryNotional: 0, entryTime: null };

  const closeTime = Date.parse(group.time);
  const candidateOpenFills = allGroups
    .filter((candidateGroup) => !candidateGroup.isClosing && candidateGroup.symbol === group.symbol)
    .flatMap((candidateGroup) => candidateGroup.fills.filter((fill) => fill.contract.secType === "OPT"))
    .filter((fill) => {
      const key = executedOptionContractKey(fill);
      if (!key || !requiredByContract.has(key)) return false;
      const openTime = Date.parse(fill.time);
      if (!Number.isNaN(closeTime) && !Number.isNaN(openTime) && openTime > closeTime) return false;
      return true;
    })
    .sort((a, b) => {
      const aTime = Date.parse(a.time);
      const bTime = Date.parse(b.time);
      if (Number.isNaN(aTime) && Number.isNaN(bTime)) return 0;
      if (Number.isNaN(aTime)) return 1;
      if (Number.isNaN(bTime)) return -1;
      return bTime - aTime;
    });

  const remainingByContract = new Map(requiredByContract);
  let matchedQty = 0;
  let netCash = 0;
  let earliestEntryTime: string | null = null;

  for (const fill of candidateOpenFills) {
    const key = executedOptionContractKey(fill);
    if (!key) continue;

    const remainingQty = remainingByContract.get(key) ?? 0;
    if (remainingQty <= 0) continue;
    if (fill.avgPrice == null || !Number.isFinite(fill.avgPrice)) continue;

    const takeQty = Math.min(remainingQty, Math.abs(fill.quantity));
    if (takeQty <= 0) continue;

    const cashSign = fill.side === "SLD" || fill.side === "SELL"
      ? 1
      : fill.side === "BOT" || fill.side === "BUY"
        ? -1
        : 0;
    if (cashSign === 0) continue;

    netCash += cashSign * fill.avgPrice * takeQty;
    matchedQty += takeQty;
    remainingByContract.set(key, remainingQty - takeQty);

    // Track earliest entry time among matched fills
    if (fill.time) {
      if (!earliestEntryTime) {
        earliestEntryTime = fill.time;
      } else {
        const fillTime = Date.parse(fill.time);
        const currentEarliest = Date.parse(earliestEntryTime);
        if (!Number.isNaN(fillTime) && !Number.isNaN(currentEarliest) && fillTime < currentEarliest) {
          earliestEntryTime = fill.time;
        }
      }
    }
  }

  const fullyMatched = [...remainingByContract.values()].every((remainingQty) => remainingQty <= 0);
  if (!fullyMatched || matchedQty <= 0) return { entryPrice: null, entryNotional: 0, entryTime: null };

  const comboUnits = Math.max(group.totalQuantity, 1);
  return {
    entryPrice: -(netCash / comboUnits),
    entryNotional: Math.abs(netCash) * 100,
    entryTime: earliestEntryTime,
  };
}

/** Build share data for a position group (aggregated fills).
 *  For BAG/combo closing groups, uses the matching opening group's net combo
 *  price as cost basis for accurate P&L % (e.g. risk reversal opened at $0.25
 *  credit, closed at $2.50 = +900%, not the misleading ~21% from leg notionals).
 *
 *  @param group - The position fill group to build share data for
 *  @param allGroups - All position groups (for finding matching opening fills)
 *  @param portfolioPositions - Portfolio positions (fallback for entry data when opening fills not in allGroups)
 */
export function positionGroupShareData(
  group: PositionFillGroup,
  allGroups?: PositionFillGroup[],
  portfolioPositions?: readonly PortfolioPosition[],
  tradeLogDates?: Record<string, string>,
): SharePnlData {
  let pnlPct: number | null = null;
  let entryPrice: number | null = null;
  let entryTime: string | null = null;

  if (group.totalPnL != null && group.isClosing) {
    const hasBagFills = group.fills.some((f) => f.contract.secType === "BAG");
    let entryNotional = 0;

    if (hasBagFills && allGroups) {
      const openingBasis = resolveOpeningLegBasis(group, allGroups);
      entryPrice = openingBasis.entryPrice;
      entryNotional = openingBasis.entryNotional;
      entryTime = openingBasis.entryTime;
    }

    // Fallback for non-BAG closing groups: find matching opening fills
    if (!hasBagFills && allGroups) {
      const openingBasis = resolveOpeningLegBasis(group, allGroups);
      if (openingBasis.entryPrice != null) {
        entryPrice = openingBasis.entryPrice;
        entryTime = openingBasis.entryTime;
        if (entryNotional === 0) {
          entryNotional = openingBasis.entryNotional;
        }
      }
    }

    // Fallback to portfolio position data if we couldn't find opening fills
    // (happens when position was opened on a previous day)
    if (entryPrice == null && portfolioPositions) {
      // Match by ticker AND structure to avoid picking up a different position
      // on the same underlying (e.g., new PLTR Bull Call Spread vs closed PLTR Long Call).
      // Extract key structure words from the group description for fuzzy matching.
      const descWords = group.description.replace(/[()$,]/g, " ").toLowerCase().split(/\s+/).filter(Boolean);
      const matchingPosition = portfolioPositions.find((p) => {
        if (p.ticker !== group.symbol) return false;
        const posWords = p.structure.replace(/[()$,]/g, " ").toLowerCase().split(/\s+/).filter(Boolean);
        // At least 2 key words must overlap (e.g., "long" + "call", or "bull" + "spread")
        const overlap = posWords.filter((w) => descWords.includes(w));
        return overlap.length >= 2;
      });
      if (matchingPosition) {
        // Calculate per-unit entry price from legs
        // For single-leg positions, use avg_cost directly
        // For multi-leg, sum up the leg costs and divide by contracts
        if (matchingPosition.legs.length === 1) {
          entryPrice = matchingPosition.legs[0].avg_cost;
        } else if (matchingPosition.legs.length > 1 && matchingPosition.contracts > 0) {
          // Net entry price for combo = sum of (direction-adjusted avg_cost per leg)
          const netCost = matchingPosition.legs.reduce((sum, leg) => {
            const sign = leg.direction === "LONG" ? -1 : 1; // Long = paid, Short = received
            return sum + sign * leg.avg_cost;
          }, 0);
          entryPrice = netCost;
        }
        // Use entry_date from portfolio (date only, no time)
        if (matchingPosition.entry_date) {
          entryTime = matchingPosition.entry_date;
        }
        // Calculate notional for P&L %
        if (entryNotional === 0 && entryPrice != null) {
          entryNotional = Math.abs(entryPrice) * (matchingPosition.contracts || group.totalQuantity) * 100;
        }
      }
    }

    // Fallback for fully-closed positions no longer in portfolio:
    // derive entry price from exit price and realized P&L.
    // entryPrice = exitPrice - realizedPNL / (quantity * multiplier)
    if (entryPrice == null && group.totalPnL != null) {
      const optFills = group.fills.filter((f) => f.contract.secType === "OPT");
      const totalQty = optFills.reduce((sum, f) => sum + f.quantity, 0);
      // Derive exit price: BAG netPrice, or weighted avg of OPT fills
      let exitPx = group.netPrice;
      if (exitPx == null && totalQty > 0) {
        const weightedSum = optFills.reduce((s, f) => s + (f.avgPrice ?? 0) * f.quantity, 0);
        exitPx = weightedSum / totalQty;
      }
      if (totalQty > 0 && exitPx != null) {
        const mult = optFills[0]?.contract.secType === "OPT" ? 100 : 1;
        entryPrice = exitPx - (group.totalPnL / (totalQty * mult));
        if (entryNotional === 0) {
          entryNotional = Math.abs(entryPrice) * totalQty * mult;
        }
      }
    }

    if (entryNotional > 0) {
      pnlPct = (group.totalPnL / entryNotional) * 100;
    } else {
      // Fallback: derive entry notional from exit notional - P&L
      // Return on Risk = P&L / Capital at Risk (entry cost)
      const optFills = group.fills.filter((f) => f.contract.secType === "OPT");
      const exitNotional = optFills.reduce((sum, f) => {
        const mult = f.contract.secType === "OPT" ? 100 : 1;
        return sum + Math.abs((f.avgPrice ?? 0) * f.quantity * mult);
      }, 0);
      const derivedEntry = Math.abs(exitNotional - (group.totalPnL ?? 0));
      if (derivedEntry > 0) {
        entryNotional = derivedEntry;
        pnlPct = (group.totalPnL / derivedEntry) * 100;
      }
    }
  }

  // Fallback entry time from trade_log for fully-closed positions
  if (entryTime == null && tradeLogDates?.[group.symbol]) {
    entryTime = tradeLogDates[group.symbol];
  }

  // Exit time is the closing group's time
  const exitTime = group.isClosing ? group.time : null;

  return {
    description: group.description,
    pnl: group.totalPnL ?? 0,
    pnlPct,
    commission: group.totalCommission,
    fillPrice: group.netPrice,
    entryPrice,
    exitPrice: group.isClosing ? group.netPrice : null,
    entryTime,
    exitTime,
    time: group.time ? new Date(group.time).toLocaleString() : "",
  };
}

/* ─── Executed Orders: Position Grouping ───────────────────────────────────
 * Groups individual IB fills into position-level rows (opening / closing).
 * BAG fills are the combo order envelope; OPT fills are the individual legs.
 * Fills within 60s of each other for the same underlying are one position group.
 */
export type PositionFillGroup = {
  id: string;
  symbol: string;
  description: string;
  isClosing: boolean;
  totalQuantity: number;
  netPrice: number | null;
  totalCommission: number;
  totalPnL: number | null;
  time: string;
  fills: ExecutedOrder[];
};

function deriveGroupDescription(
  fills: ExecutedOrder[],
  isClosing: boolean,
  portfolioPositions?: readonly PortfolioPosition[],
): string {
  return buildExecutedGroupDescription(fills, isClosing, portfolioPositions);
}

function groupExecutedOrders(
  fills: ExecutedOrder[],
  portfolioPositions?: readonly PortfolioPosition[],
): PositionFillGroup[] {
  if (fills.length === 0) return [];

  // Separate cancelled orders (keep as-is, ungrouped)
  const cancelled = fills.filter((f) => f.side === "CANCELLED");
  const real = fills.filter((f) => f.side !== "CANCELLED");

  const isClosingFill = (fill: ExecutedOrder): boolean =>
    fill.contract.secType === "OPT" && fill.realizedPNL != null && Math.abs(fill.realizedPNL) > 0.01;

  type MinuteBucket = {
    symbol: string;
    opens: ExecutedOrder[];
    closes: ExecutedOrder[];
    bags: ExecutedOrder[];
  };

  const byMinute = new Map<string, MinuteBucket>();
  for (const fill of real) {
    const sym = fill.contract.symbol;
    const t = new Date(fill.time);
    // Round time to nearest minute for grouping
    const bucket = new Date(t.getFullYear(), t.getMonth(), t.getDate(), t.getHours(), t.getMinutes()).toISOString();
    const key = `${sym}_${bucket}`;
    const bucketData = byMinute.get(key);
    if (bucketData == null) {
      byMinute.set(key, {
        symbol: sym,
        opens: [],
        closes: [],
        bags: [],
      });
    }

    const target = byMinute.get(key);
    if (!target) continue;

    if (fill.contract.secType === "BAG") {
      target.bags.push(fill);
    } else if (isClosingFill(fill)) {
      target.closes.push(fill);
    } else {
      target.opens.push(fill);
    }
  }

  const assignBagToBucket = (
    bag: ExecutedOrder,
    bucketSideFills: ExecutedOrder[],
    fallback: ExecutedOrder[],
  ): ExecutedOrder[] => {
    if (bucketSideFills.length === 0) return [...fallback];
    const bagTime = Date.parse(bag.time);
    const safeBagTime = Number.isNaN(bagTime) ? null : bagTime;
    let bestSide: ExecutedOrder[] = [];
    let bestDistance = Number.POSITIVE_INFINITY;

    for (const targetFill of bucketSideFills) {
      const targetTime = Date.parse(targetFill.time);
      if (Number.isNaN(targetTime) || safeBagTime == null) continue;
      const delta = Math.abs(targetTime - safeBagTime);
      if (delta < bestDistance) {
        bestDistance = delta;
        bestSide = [targetFill];
      }
    }

    if (!Number.isFinite(bestDistance)) return [...fallback];
    return bestSide;
  };

  const makeGroup = (
    groupFills: ExecutedOrder[],
    isClosing: boolean,
  ): PositionFillGroup => {
    const optFills = groupFills.filter((f) => f.contract.secType !== "BAG");
    const sym = groupFills[0].contract.symbol;
    const bagFills = groupFills.filter((f) => f.contract.secType === "BAG");
    const totalQty = bagFills.length > 0
      ? bagFills.reduce((sum, f) => sum + f.quantity, 0)
      : optFills.reduce((sum, f) => sum + f.quantity, 0);

    // Net price: BAG fill has the combo price, single-leg uses weighted avg
    let netPrice: number | null = null;
    if (bagFills.length > 0 && bagFills[0].avgPrice != null) {
      netPrice = bagFills[0].avgPrice;
    } else if (optFills.length > 0) {
      const totalQty = optFills.reduce((s, f) => s + f.quantity, 0);
      const weightedSum = optFills.reduce((s, f) => s + (f.avgPrice ?? 0) * f.quantity, 0);
      netPrice = totalQty > 0 ? Number((weightedSum / totalQty).toFixed(4)) : null;
    }

    const totalCommission = optFills.reduce((sum, f) => sum + (f.commission ?? 0), 0);
    const totalPnL = isClosing
      ? optFills.reduce((sum, f) => sum + (f.realizedPNL ?? 0), 0)
      : null;

    const latestTime = groupFills.reduce((maxTime, f) => {
      const current = Date.parse(f.time);
      const previous = Date.parse(maxTime);
      if (Number.isNaN(current)) return maxTime;
      if (Number.isNaN(previous)) return f.time;
      return current > previous ? f.time : maxTime;
    }, groupFills[0].time);

    return {
      id: `${sym}_${Date.parse(groupFills[0].time).toString()}`,
      symbol: sym,
      description: deriveGroupDescription(groupFills, isClosing, portfolioPositions),
      isClosing,
      totalQuantity: totalQty,
      netPrice,
      totalCommission,
      totalPnL,
      time: latestTime,
      fills: groupFills,
    };
  };

  const nextId = (() => {
    let id = 0;
    return () => {
      id += 1;
      return `position-group-${id}`;
    };
  })();

  const result: PositionFillGroup[] = [];
  for (const bucket of byMinute.values()) {
    const { opens, closes, bags } = bucket;
    if (opens.length > 0 && closes.length > 0) {
      const closeBuckets: ExecutedOrder[] = [];
      const openBuckets: ExecutedOrder[] = [];

      for (const bag of bags) {
        const closeDistances = assignBagToBucket(bag, closes, closes);
        const openDistances = assignBagToBucket(bag, opens, opens);
        if (closeDistances.length > 0 && openDistances.length > 0) {
          // If both have valid distances, pick the nearer side.
          const bagTime = Date.parse(bag.time);
          const closeDist = closeDistances.map((f) => Math.abs(Date.parse(f.time) - bagTime))[0];
          const openDist = openDistances.map((f) => Math.abs(Date.parse(f.time) - bagTime))[0];
          if (closeDist <= openDist) closeBuckets.push(bag);
          else openBuckets.push(bag);
        } else if (closeDistances.length > 0) {
          closeBuckets.push(bag);
        } else {
          openBuckets.push(bag);
        }
      }

      const closeGroupFills = [...closes, ...closeBuckets];
      const openGroupFills = [...opens, ...openBuckets];

      if (closeGroupFills.length > 0) {
        result.push({ ...makeGroup(closeGroupFills, true), id: `${nextId()}-close` });
      }
      if (openGroupFills.length > 0) {
        result.push({ ...makeGroup(openGroupFills, false), id: `${nextId()}-open` });
      }
      continue;
    }

    if (closes.length > 0) {
      result.push({ ...makeGroup([...closes, ...bags], true), id: `${nextId()}-close` });
    } else if (opens.length > 0) {
      result.push({ ...makeGroup([...opens, ...bags], false), id: `${nextId()}-open` });
    }
  }

  // Add cancelled orders as individual groups
  for (const c of cancelled) {
    result.push({
      id: c.execId,
      symbol: c.contract.symbol || c.symbol,
      description: `Cancelled ${c.symbol}`,
      isClosing: false,
      totalQuantity: c.quantity,
      netPrice: c.avgPrice,
      totalCommission: 0,
      totalPnL: null,
      time: c.time,
      fills: [c],
    });
  }

  // Sort by latest execution time descending
  result.sort((a, b) => {
    const bMs = Date.parse(b.time);
    const aMs = Date.parse(a.time);
    if (Number.isNaN(aMs) && Number.isNaN(bMs)) return 0;
    if (Number.isNaN(aMs)) return 1;
    if (Number.isNaN(bMs)) return -1;
    return bMs - aMs;
  });
  return result;
}

function blotterShareData(t: BlotterTrade): SharePnlData {
  const lastExec = t.executions.length > 0 ? t.executions[t.executions.length - 1] : null;
  const pnlPct = t.cost_basis !== 0 ? (t.realized_pnl / Math.abs(t.cost_basis)) * 100 : null;
  // Derive per-unit entry/exit from execution prices (weighted average)
  let entryPrice: number | null = null;
  let exitPrice: number | null = null;
  if (t.executions.length >= 2) {
    const firstSide = t.executions[0].side;
    const openExecs = t.executions.filter((e) => e.side === firstSide);
    const closeExecs = t.executions.filter((e) => e.side !== firstSide);
    if (openExecs.length > 0) {
      const totalQty = openExecs.reduce((s, e) => s + e.quantity, 0);
      const totalVal = openExecs.reduce((s, e) => s + e.price * e.quantity, 0);
      entryPrice = totalQty > 0 ? totalVal / totalQty : null;
    }
    if (closeExecs.length > 0) {
      const totalQty = closeExecs.reduce((s, e) => s + e.quantity, 0);
      const totalVal = closeExecs.reduce((s, e) => s + e.price * e.quantity, 0);
      exitPrice = totalQty > 0 ? totalVal / totalQty : null;
    }
  }
  // Derive entry and exit times from executions
  let entryTime: string | null = null;
  let exitTime: string | null = null;
  if (t.executions.length >= 2) {
    const firstSide = t.executions[0].side;
    const openExecs = t.executions.filter((e) => e.side === firstSide);
    const closeExecs = t.executions.filter((e) => e.side !== firstSide);
    if (openExecs.length > 0 && openExecs[0].time) {
      // Use earliest opening execution time
      entryTime = openExecs.reduce((earliest, e) => {
        if (!e.time) return earliest;
        if (!earliest) return e.time;
        return Date.parse(e.time) < Date.parse(earliest) ? e.time : earliest;
      }, null as string | null);
    }
    if (closeExecs.length > 0 && closeExecs[closeExecs.length - 1].time) {
      // Use latest closing execution time
      exitTime = closeExecs.reduce((latest, e) => {
        if (!e.time) return latest;
        if (!latest) return e.time;
        return Date.parse(e.time) > Date.parse(latest) ? e.time : latest;
      }, null as string | null);
    }
  }

  return {
    description: t.contract_desc || t.symbol,
    pnl: t.realized_pnl,
    pnlPct,
    commission: t.total_commission,
    fillPrice: lastExec?.price ?? null,
    entryPrice,
    exitPrice,
    entryTime,
    exitTime,
    time: lastExec?.time ? new Date(lastExec.time).toLocaleString() : "",
  };
}

/* ─── Sortable header cell ──────────────────────────────── */

function SortTh<K extends string>({
  label,
  sortKey,
  activeKey,
  direction,
  onToggle,
  className,
}: {
  label: string;
  sortKey: K;
  activeKey: K | null;
  direction: SortDirection;
  onToggle: (key: K) => void;
  className?: string;
}) {
  const active = activeKey === sortKey;
  const ariaSort = active ? (direction === "asc" ? "ascending" : "descending") : undefined;
  return (
    <th
      className={`sortable-th ${className ?? ""} ${active ? "sort-active" : ""}`}
      onClick={() => onToggle(sortKey)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(sortKey); } }}
      tabIndex={0}
      role="columnheader"
      aria-sort={ariaSort}
    >
      <span className="sort-label">
        {label}
        <span className="sort-icon">
          {active ? (
            direction === "asc" ? <ChevronUp size={10} /> : <ChevronDown size={10} />
          ) : (
            <ChevronDown size={10} className="sort-icon-idle" />
          )}
        </span>
      </span>
    </th>
  );
}

/* ─── Price direction hook (local, used by OrderPriceCell) ── */

function usePriceDirection(price: number | null): {
  direction: "up" | "down" | null;
  flashDirection: "up" | "down" | null;
} {
  const [direction, setDirection] = useState<"up" | "down" | null>(null);
  const [flashDirection, setFlashDirection] = useState<"up" | "down" | null>(null);
  const previousPrice = useRef<number | null>(null);

  useEffect(() => {
    const previous = previousPrice.current;

    if (previous == null || price == null) {
      setDirection(null);
      setFlashDirection(null);
      previousPrice.current = price;
      return undefined;
    }

    if (price > previous) {
      setDirection("up");
      setFlashDirection("up");
    } else if (price < previous) {
      setDirection("down");
      setFlashDirection("down");
    } else {
      setFlashDirection(null);
    }

    previousPrice.current = price;

    if (price !== previous) {
      const timer = setTimeout(() => setFlashDirection(null), 2500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [price]);

  return { direction, flashDirection };
}

/* ─── Flow tables ───────────────────────────────────────── */

type FlowPosKey = "ticker" | "position" | "flow_label" | "strength" | "note";

const flowPosExtract = (item: FlowAnalysisPosition, key: FlowPosKey): string | number => {
  if (key === "strength") return item.strength;
  return item[key];
};

function FlowSparkline({ ratios }: { ratios?: { date: string; buy_ratio: number | null }[] }) {
  if (!ratios || ratios.length === 0) return <div className="strength-value">---</div>;
  const maxH = 28;
  return (
    <div className="flow-sparkline">
      {ratios.map((d, i) => {
        const r = d.buy_ratio;
        if (r == null) return <div key={i} className="flow-spark-bar neutral" style={{ height: 2 }} />;
        const cls = r >= 0.55 ? "accum" : r <= 0.45 ? "distrib" : "neutral";
        const h = Math.max(2, Math.round(r * maxH));
        return <div key={i} className={`flow-spark-bar ${cls}`} style={{ height: h }} title={`${d.date}: ${Math.round(r * 100)}%`} />;
      })}
    </div>
  );
}

function FlowTable({ rows, lastColumn }: { rows: FlowAnalysisPosition[]; lastColumn: string }) {
  const { sorted, sort, toggle } = useSort(rows, flowPosExtract);
  return (
    <table>
      <thead>
        <tr>
          <SortTh<FlowPosKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Position" sortKey="position" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Flow" sortKey="flow_label" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Strength" sortKey="strength" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label={lastColumn} sortKey="note" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <tr key={`${item.ticker}-${item.position}`}>
            <td><TickerLink ticker={item.ticker} /></td>
            <td>{item.position}</td>
            <td><span className={`pill ${item.flow_class}`}>{item.flow_label}</span></td>
            <td>
              <FlowSparkline ratios={item.daily_buy_ratios} />
              <div className="strength-value">{item.strength}</div>
            </td>
            <td>{item.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FlowSections() {
  const { data, syncing, error, lastSync } = useFlowAnalysis(true);

  const supportsArr = data?.supports ?? [];
  const againstArr = data?.against ?? [];
  const watchArr = data?.watch ?? [];
  const neutralArr = data?.neutral ?? [];
  const totalScanned = data?.positions_scanned ?? 0;

  // Action items = against positions (flow contradicts trade direction)
  const actionItems = againstArr.filter((p) => p.strength >= 15);

  return (
    <>
      {actionItems.length > 0 && (
        <div className="section">
          <div className="alert-box">
            <div className="alert-title">
              <TriangleAlert size={14} />
              ACTION ITEMS
            </div>
            {actionItems.map((item) => (
              <div key={`${item.ticker}-${item.position}`} className="alert-item">
                <span className="alert-ticker">{item.ticker}</span> — {item.position}: {item.note}
              </div>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="section">
          <div className="section-body"><div className="alert-item bearish">{error}</div></div>
        </div>
      )}

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <CheckCircle2 size={14} />
            Flow Supports Position
            <InfoTooltip text={SECTION_TOOLTIPS["Flow Supports Position"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${supportsArr.length} POSITIONS`}
            </span>
          </div>
        </div>
        <div className="section-body">
          {supportsArr.length > 0 ? (
            <FlowTable rows={supportsArr} lastColumn="Signal" />
          ) : (
            <div className="alert-item">{syncing ? "Scanning portfolio flow..." : "No supporting flow detected"}</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <TrendingDown size={14} />
            Flow Against Position
            <InfoTooltip text={SECTION_TOOLTIPS["Flow Against Position"]} />
          </div>
          <span className="pill distrib">{againstArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {againstArr.length > 0 ? (
            <FlowTable rows={againstArr} lastColumn="Concern" />
          ) : (
            <div className="alert-item">No contradicting flow detected</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Circle size={14} />
            Neutral / Low Signal
            <InfoTooltip text={SECTION_TOOLTIPS["Neutral / Low Signal"]} />
          </div>
          <span className="pill neutral">{neutralArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {neutralArr.length > 0 ? (
            <FlowTable rows={neutralArr} lastColumn="Note" />
          ) : (
            <div className="alert-item">No neutral positions</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Bell size={14} />
            Watch Closely
            <InfoTooltip text={SECTION_TOOLTIPS["Watch Closely"]} />
          </div>
          <span className="pill undefined">{watchArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {watchArr.length > 0 ? (
            <FlowTable rows={watchArr} lastColumn="Note" />
          ) : (
            <div className="alert-item">No watch items</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="report-meta">
          {lastSync
            ? `Report Generated: ${new Date(lastSync).toLocaleString()} • Source: UW API • Dark Pool Lookback: 5 Trading Days • ${totalScanned} Positions Scanned`
            : "Awaiting initial flow analysis..."}
        </div>
      </div>
    </>
  );
}

/* ─── Portfolio sections ──────────────────────────────────── */

function PortfolioSections({ portfolio, prices }: { portfolio: PortfolioData | null; prices?: Record<string, PriceData> }) {
  if (!portfolio) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Circle size={14} />
            Portfolio
            <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body">
          <div className="alert-item">Waiting for portfolio data...</div>
        </div>
      </div>
    );
  }

  const definedPositions = portfolio.positions.filter((p) => p.risk_profile === "defined");
  const equityPositions = portfolio.positions.filter((p) => p.risk_profile === "equity");
  const undefinedPositions = portfolio.positions.filter((p) => p.risk_profile === "undefined" || p.risk_profile === "complex");

  const extractPositionSearchText = useCallback(
    (p: PortfolioPosition) => `${p.ticker} ${p.structure} ${p.direction} ${p.expiry}`,
    [],
  );
  const definedFilter = useTableFilter(definedPositions, extractPositionSearchText);
  const undefinedFilter = useTableFilter(undefinedPositions, extractPositionSearchText);
  const equityFilter = useTableFilter(equityPositions, extractPositionSearchText);

  return (
    <>
      {definedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <CheckCircle2 size={14} />
              Defined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <TableSearch
                query={definedFilter.query}
                setQuery={definedFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={definedFilter.filtered.length}
                totalCount={definedPositions.length}
              />
              <span className="pill defined">{definedPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={definedFilter.filtered} showUnderlying={true} prices={prices} />
          </div>
        </div>
      )}

      {undefinedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <TriangleAlert size={14} />
              Undefined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Undefined Risk Positions"]} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <TableSearch
                query={undefinedFilter.query}
                setQuery={undefinedFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={undefinedFilter.filtered.length}
                totalCount={undefinedPositions.length}
              />
              <span className="pill undefined">{undefinedPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={undefinedFilter.filtered} showUnderlying={true} prices={prices} />
          </div>
        </div>
      )}

      {equityPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Circle size={14} />
              Equity Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Equity Positions"]} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <TableSearch
                query={equityFilter.query}
                setQuery={equityFilter.setQuery}
                placeholder="Filter positions..."
                resultCount={equityFilter.filtered.length}
                totalCount={equityPositions.length}
              />
              <span className="pill neutral">{equityPositions.length} POSITIONS</span>
            </div>
          </div>
          <div className="section-body">
            <PositionTable positions={equityFilter.filtered} showExpiry={false} prices={prices} />
          </div>
        </div>
      )}

      <div className="section">
        <div className="report-meta">
          Last Sync: {new Date(portfolio.last_sync).toLocaleString()} • Source: IB Gateway (4001)
        </div>
      </div>
    </>
  );
}

/* ─── Scanner table ─────────────────────────────────────── */

type ScannerSortKey = "ticker" | "signal" | "direction" | "score" | "strength" | "buy_ratio" | "sustained_days" | "num_prints";

const scannerSigExtract = (item: ScannerSignal, key: ScannerSortKey): string | number | null => {
  switch (key) {
    case "ticker": return item.ticker;
    case "signal": return item.signal;
    case "direction": return item.direction;
    case "score": return item.score;
    case "strength": return item.strength;
    case "buy_ratio": return item.buy_ratio;
    case "sustained_days": return item.sustained_days;
    case "num_prints": return item.num_prints;
    default: return null;
  }
};

function ScannerSections() {
  const { data, syncing, error, lastSync } = useScanner(true);
  const signals = data?.top_signals ?? [];
  const { sorted, sort, toggle } = useSort(signals, scannerSigExtract);

  const signalClass = (signal: string) => {
    if (signal === "STRONG") return "bullish";
    if (signal === "MODERATE") return "neutral";
    return "bearish";
  };

  const dirClass = (dir: string) => {
    if (dir === "ACCUMULATION") return "accum";
    if (dir === "DISTRIBUTION") return "distrib";
    return "neutral";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Sparkles size={14} />
            Scanner Signals
            <InfoTooltip text={SECTION_TOOLTIPS["Scanner Signals"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${data?.signals_found ?? 0} SIGNALS`}
            </span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {signals.length === 0 && !syncing && !error && (
          <div className="section-body"><div className="alert-item">No scanner signals. Waiting for initial scan...</div></div>
        )}
        {signals.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<ScannerSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Strength" sortKey="strength" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Buy Ratio" sortKey="buy_ratio" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Sustained" sortKey="sustained_days" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Prints" sortKey="num_prints" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={`scanner-${row.ticker}`}>
                    <td><TickerLink ticker={row.ticker} /></td>
                    <td><span className={signalClass(row.signal)}>{row.signal}</span></td>
                    <td><span className={`pill ${dirClass(row.direction)}`}>{row.direction}</span></td>
                    <td className="right">{row.score.toFixed(1)}</td>
                    <td className="right">{row.strength.toFixed(1)}</td>
                    <td className="right">{row.buy_ratio != null ? `${(row.buy_ratio * 100).toFixed(1)}%` : "—"}</td>
                    <td className="right">{row.sustained_days > 0 ? `${row.sustained_days}d` : "—"}</td>
                    <td className="right">{row.num_prints.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {lastSync && (
        <div className="section">
          <div className="report-meta">
            Last Scan: {new Date(lastSync).toLocaleString()} • {data?.tickers_scanned ?? 0} Tickers Scanned
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Non-table sections ────────────────────────────────── */

type DiscoverSortKey = "ticker" | "score" | "dp_direction" | "dp_strength" | "dp_buy_ratio" | "options_bias" | "alerts" | "total_premium" | "sweeps" | "sector";

const discoverExtract = (item: DiscoverCandidate, key: DiscoverSortKey): string | number | null => {
  switch (key) {
    case "ticker": return item.ticker;
    case "score": return item.score;
    case "dp_direction": return item.dp_direction;
    case "dp_strength": return item.dp_strength;
    case "dp_buy_ratio": return item.dp_buy_ratio;
    case "options_bias": return item.options_bias;
    case "alerts": return item.alerts;
    case "total_premium": return item.total_premium;
    case "sweeps": return item.sweeps;
    case "sector": return item.sector || item.issue_type || "";
    default: return null;
  }
};

function DiscoverSections() {
  const { data, syncing, error, lastSync } = useDiscover(true);
  const candidates = data?.candidates ?? [];
  const { sorted, sort, toggle } = useSort<DiscoverCandidate, DiscoverSortKey>(candidates, discoverExtract, "score", "desc");

  const fmtPremium = (v: number) => {
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  };

  const biasClass = (bias: string) => {
    if (bias === "BULLISH" || bias === "CALLS") return "bullish";
    if (bias === "BEARISH" || bias === "PUTS") return "bearish";
    return "neutral";
  };

  const dpClass = (dir: string) => {
    if (dir === "ACCUMULATION") return "bullish";
    if (dir === "DISTRIBUTION") return "bearish";
    return "neutral";
  };

  const scoreClass = (score: number) => {
    if (score >= 60) return "bullish";
    if (score >= 40) return "neutral";
    return "bearish";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Search size={14} />
            Discovery Candidates
            <InfoTooltip text={SECTION_TOOLTIPS["Discovery Candidates"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${candidates.length} FOUND`}
            </span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {candidates.length === 0 && !syncing && !error && (
          <div className="section-body"><div className="alert-item">No candidates found. Waiting for initial scan...</div></div>
        )}
        {candidates.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<DiscoverSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="DP Direction" sortKey="dp_direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="DP Strength" sortKey="dp_strength" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Buy Ratio" sortKey="dp_buy_ratio" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Options Bias" sortKey="options_bias" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Alerts" sortKey="alerts" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Premium" sortKey="total_premium" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Sweeps" sortKey="sweeps" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Sector" sortKey="sector" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((c) => (
                  <tr key={c.ticker}>
                    <td><TickerLink ticker={c.ticker} /></td>
                    <td className="right">
                      <span className={scoreClass(c.score)}>{c.score.toFixed(1)}</span>
                    </td>
                    <td><span className={dpClass(c.dp_direction)}>{c.dp_direction}</span></td>
                    <td className="right">{c.dp_strength.toFixed(1)}</td>
                    <td className="right">{(c.dp_buy_ratio * 100).toFixed(1)}%</td>
                    <td><span className={biasClass(c.options_bias)}>{c.options_bias}</span></td>
                    <td className="right">{c.alerts}</td>
                    <td className="right">{fmtPremium(c.total_premium)}</td>
                    <td className="right">{c.sweeps}</td>
                    <td className="cell-muted">{c.sector || c.issue_type || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

type JournalSortKey = "id" | "date" | "ticker" | "structure" | "decision" | "qty" | "entry_cost" | "max_risk" | "realized_pnl" | "ror";

const journalSortExtract = (t: TradeEntry, key: JournalSortKey): string | number | null => {
  switch (key) {
    case "id": return t.id;
    case "date": return t.date;
    case "ticker": return t.ticker;
    case "structure": return t.structure;
    case "decision": return t.decision;
    case "qty": return t.contracts ?? t.shares ?? t.quantity ?? null;
    case "entry_cost": return t.total_cost ?? t.entry_cost ?? null;
    case "max_risk": return t.max_risk ?? null;
    case "realized_pnl": return t.realized_pnl ?? null;
    case "ror": return t.return_on_risk ?? null;
    default: return null;
  }
};

function JournalSections() {
  const { data, loading, error, syncWithIB, syncing, lastSyncResult } = useJournal();
  const [syncError, setSyncError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const trades = useMemo(() => {
    if (!data?.trades) return [];
    return [...data.trades].sort((a, b) => b.id - a.id);
  }, [data]);

  const extractSearchText = useCallback(
    (t: TradeEntry) => `${t.ticker} ${t.structure} ${t.decision} ${t.date} ${t.edge_analysis?.edge_type ?? ""}`,
    [],
  );
  const { filtered, query, setQuery } = useTableFilter(trades, extractSearchText);
  const { sorted: sortedTrades, sort, toggle } = useSort(filtered, journalSortExtract, "id" as JournalSortKey, "desc");

  const toggleExpand = useCallback((id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const handleSync = useCallback(async () => {
    setSyncError(null);
    try {
      await syncWithIB();
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : "Sync failed");
    }
  }, [syncWithIB]);

  const fmtJournalUsd = (v: number | undefined | null) => {
    if (v == null) return "—";
    const abs = Math.abs(v);
    const formatted = abs >= 1000 ? `$${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}` : `$${abs.toFixed(2)}`;
    return v < 0 ? `-${formatted}` : formatted;
  };

  const decisionClass = (d: string) => {
    if (d === "EXECUTED" || d === "OPEN") return "bullish";
    if (d === "CLOSED") return "neutral";
    if (d === "FREED" || d === "CONVERTED") return "lean-bullish";
    if (d === "IB_AUTO_IMPORT") return "ib-import";
    return "bearish";
  };

  const pnlClass = (v: number | undefined | null) => {
    if (v == null) return "";
    return v >= 0 ? "bullish" : "bearish";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Wrench size={14} />
            Trade Journal
            <InfoTooltip text={SECTION_TOOLTIPS["Trade Journal"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button
              className="btn-sync"
              onClick={handleSync}
              disabled={syncing}
              title="Sync unreconciled IB trades into journal"
            >
              {syncing ? "SYNCING..." : "SYNC IB"}
            </button>
            {lastSyncResult && (
              <span className="pill defined" style={{ fontSize: "9px" }}>
                {lastSyncResult.imported > 0
                  ? `+${lastSyncResult.imported} IMPORTED`
                  : "UP TO DATE"}
              </span>
            )}
            {trades.length > 0 ? (
              <TableSearch
                query={query}
                setQuery={setQuery}
                placeholder="Filter trades..."
                resultCount={filtered.length}
                totalCount={trades.length}
              />
            ) : null}
            <span className="pill defined">{trades.length} TRADES</span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {syncError && <div className="section-body"><div className="alert-item bearish">IB Sync: {syncError}</div></div>}
        {loading && <div className="section-body p-6"><TableSkeleton rows={4} columns={6} /></div>}
        {!loading && trades.length === 0 && !error && (
          <div className="section-body"><div className="alert-item">No trades in journal.</div></div>
        )}
        {trades.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<JournalSortKey> label="#" sortKey="id" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Date" sortKey="date" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Structure" sortKey="structure" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Status" sortKey="decision" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Qty" sortKey="qty" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Entry Cost" sortKey="entry_cost" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Max Risk" sortKey="max_risk" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Realized P&L" sortKey="realized_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="RoR" sortKey="ror" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th>Gates</th>
                  <th>Edge</th>
                </tr>
              </thead>
              <tbody>
                {sortedTrades.map((t) => {
                  const qty = t.contracts ?? t.shares ?? t.quantity ?? null;
                  const cost = t.total_cost ?? t.entry_cost ?? null;
                  const hasLegs = t.legs && t.legs.length > 0;
                  const isExpanded = expandedIds.has(t.id);
                  return (
                    <React.Fragment key={t.id}>
                      <tr>
                        <td className="cell-muted">{t.id}</td>
                        <td>{t.date}</td>
                        <td>
                          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                            <TickerLink ticker={t.ticker} />
                            {hasLegs && (
                              <button
                                className="expand-btn"
                                onClick={() => toggleExpand(t.id)}
                                title={isExpanded ? "Collapse legs" : "Expand legs"}
                              >
                                <ChevronDown size={12} style={{ transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 150ms ease" }} />
                              </button>
                            )}
                          </span>
                        </td>
                        <td>{t.structure}</td>
                        <td><span className={decisionClass(t.decision)}>{t.decision}</span></td>
                        <td className="right">{qty ?? "—"}</td>
                        <td className="right">{fmtJournalUsd(cost)}</td>
                        <td className="right">{fmtJournalUsd(t.max_risk)}</td>
                        <td className="right"><span className={pnlClass(t.realized_pnl)}>{fmtJournalUsd(t.realized_pnl)}{t.return_on_risk != null ? ` (${(t.return_on_risk * 100) >= 0 ? "+" : ""}${(t.return_on_risk * 100).toFixed(1)}%)` : ""}</span></td>
                        <td className="right">{t.return_on_risk != null ? `${(t.return_on_risk * 100).toFixed(1)}%` : "—"}</td>
                        <td className="cell-muted">{t.gates_passed?.join(", ") || t.gates_failed?.join(", ") || "—"}</td>
                        <td className="cell-muted">{t.edge_analysis?.edge_type ?? "—"}</td>
                      </tr>
                      {hasLegs && isExpanded && t.legs!.map((leg, i) => (
                        <tr key={`${t.id}-leg-${i}`} className="leg-row">
                          <td />
                          <td className="cell-muted">{leg.expiry ?? "—"}</td>
                          <td />
                          <td className="cell-muted">{leg.type ?? "—"}{leg.strike != null ? ` $${leg.strike}` : ""}</td>
                          <td />
                          <td className="right cell-muted">{leg.contracts ?? "—"}</td>
                          <td className="right cell-muted">{leg.open_price != null ? `$${leg.open_price.toFixed(2)}` : "—"}</td>
                          <td className="right cell-muted">{leg.close_price != null ? `$${leg.close_price.toFixed(2)}` : "—"}</td>
                          <td className="right"><span className={pnlClass(leg.leg_pnl)}>{leg.leg_pnl != null ? fmtJournalUsd(leg.leg_pnl) : "—"}</span></td>
                          <td />
                          <td />
                          <td />
                        </tr>
                      ))}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/* ─── Orders tables ────────────────────────────────────── */

type OpenOrderKey = "symbol" | "action" | "orderType" | "totalQuantity" | "limitPrice" | "lastPrice" | "status" | "tif" | "actions";

/** Build the prices-map key for an order's contract (option key for options, symbol for stocks). */
function orderPriceKey(contract: OpenOrder["contract"]): string | null {
  if (contract.secType === "BAG") return null;

  if (
    contract.secType === "OPT" &&
    contract.strike != null &&
    contract.right &&
    contract.expiry
  ) {
    const right = contract.right === "C" || contract.right === "P"
      ? contract.right
      : contract.right === "CALL" ? "C" : contract.right === "PUT" ? "P" : null;
    if (right) {
      const expiryClean = contract.expiry.replace(/-/g, "");
      if (expiryClean.length === 8) {
        return optionKey({ symbol: contract.symbol.toUpperCase(), expiry: expiryClean, strike: contract.strike, right });
      }
    }
  }
  return contract.symbol;
}

/**
 * Resolve the "last price" for an order.
 * For STK/OPT: use the WS price directly.
 * For BAG (spread): find the matching portfolio position and compute
 * the net mid from each leg's WS bid/ask (long leg mid − short leg mid).
 */
function resolveOrderLastPrice(
  order: OpenOrder,
  prices: Record<string, PriceData> | undefined,
  portfolio: PortfolioData | null | undefined,
): number | null {
  if (!prices) return null;
  const pk = orderPriceKey(order.contract);
  if (pk) return prices[pk]?.last ?? null;

  // BAG: compute net mid from portfolio legs
  if (order.contract.secType !== "BAG" || !portfolio) return null;
  const pos = portfolio.positions.find((p) => p.ticker === order.contract.symbol && p.legs.length > 1);
  if (!pos) return null;

  let netMid = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    if (!key) return null;
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return null;
    const mid = (lp.bid + lp.ask) / 2;
    const sign = leg.direction === "LONG" ? 1 : -1;
    netMid += sign * mid;
  }
  return Math.round(netMid * 100) / 100;
}

function makeOpenOrderExtract(
  prices?: Record<string, PriceData>,
  portfolio?: PortfolioData | null,
) {
  return (item: OpenOrderDisplayRow, key: OpenOrderKey): string | number | null => {
    switch (key) {
      case "symbol": {
        return item.kind === "combo" ? item.symbol : item.order.contract.symbol;
      }
      case "action": return item.kind === "combo" ? "COMBO" : item.order.action;
      case "orderType": return item.kind === "combo" ? item.structure : item.order.orderType;
      case "totalQuantity": return item.kind === "combo" ? item.totalQuantity : item.order.totalQuantity;
      case "limitPrice": return item.kind === "combo" ? item.limitPrice : item.order.limitPrice;
      case "lastPrice":
        return item.kind === "combo"
          ? resolveOpenOrderComboPrice(item.orders, prices)
          : resolveOrderLastPrice(item.order, prices, portfolio);
      case "status": return item.kind === "combo" ? item.status : item.order.status;
      case "tif": return item.kind === "combo" ? item.tif : item.order.tif;
      case "actions": return null;
      default: return null;
    }
  };
}

/** Wrapper so usePriceDirection can be called per-order row (hooks can't go in map callbacks). */
function OrderPriceCell({ price }: { price: number | null }) {
  const { direction, flashDirection } = usePriceDirection(price);
  return (
    <td className={`right last-price-cell ${flashDirection ? `last-price-${flashDirection}` : ""}`}>
      {price != null ? fmtPrice(price) : "—"}
      {direction === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
      {direction === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
    </td>
  );
}

type ExecOrderKey = "symbol" | "side" | "quantity" | "avgPrice" | "commission" | "realizedPNL" | "time";

const execOrderExtract = (item: ExecutedOrder, key: ExecOrderKey): string | number | null => {
  switch (key) {
    case "symbol": return item.symbol;
    case "side": return item.side;
    case "quantity": return item.quantity;
    case "avgPrice": return item.avgPrice;
    case "commission": return item.commission;
    case "realizedPNL": return item.realizedPNL;
    case "time": return item.time;
    default: return null;
  }
};

function OrdersSections({
  orders,
  prices,
  portfolio,
}: {
  orders: OrdersData | null;
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
}) {
  const { pendingCancels, pendingModifies, cancelledOrders, requestCancel, requestModify } = useOrderActions();
  const openOrderExtract = useMemo(() => makeOpenOrderExtract(prices, portfolio), [prices, portfolio]);
  const openOrderRows = useMemo(() => {
    if (!orders) return [];
    return buildOpenOrderDisplayRows(orders.open_orders, portfolio?.positions);
  }, [orders, portfolio?.positions]);
  const openSort = useSort(openOrderRows, openOrderExtract);
  const extractOpenSearch = useCallback(
    (row: OpenOrderDisplayRow) => {
      if (row.kind === "combo") return `${row.symbol} ${row.structure} combo`;
      return `${row.order.contract.symbol} ${row.order.action} ${row.order.orderType}`;
    },
    [],
  );
  const openFilter = useTableFilter(openSort.sorted, extractOpenSearch);

  const [cancelTarget, setCancelTarget] = useState<OpenOrder | null>(null);
  const [modifyTarget, setModifyTarget] = useState<{
    modalOrder: OpenOrder;
    requestOrder: OpenOrder;
    cancelOrders?: ModifyOrderRequest["cancelOrders"];
  } | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const handleCancel = useCallback(async () => {
    if (!cancelTarget) return;
    setActionLoading(true);
    await requestCancel(cancelTarget);
    setActionLoading(false);
    setCancelTarget(null);
  }, [cancelTarget, requestCancel]);

  const handleModify = useCallback(async (request: ModifyOrderRequest) => {
    if (!modifyTarget) return;
    setActionLoading(true);
    await requestModify(
      modifyTarget.requestOrder,
      modifyTarget.cancelOrders?.length
        ? { ...request, cancelOrders: modifyTarget.cancelOrders }
        : request,
    );
    setActionLoading(false);
    setModifyTarget(null);
  }, [modifyTarget, requestModify]);

  const handleCancelCombo = useCallback(async (comboOrders: OpenOrder[]) => {
    setActionLoading(true);
    try {
      for (const order of comboOrders) {
        await requestCancel(order);
      }
    } finally {
      setActionLoading(false);
    }
  }, [requestCancel]);

  // Merge cancelled orders into executed list for display (dedupe by permId)
  const allExecutedRows = useMemo(() => {
    const seen = new Set<number>();
    const cancelRows: ExecutedOrder[] = [];
    for (const c of cancelledOrders) {
      if (seen.has(c.permId)) continue;
      seen.add(c.permId);
      cancelRows.push({
        execId: `cancelled-${c.permId}`,
        symbol: c.symbol,
        contract: { conId: null, symbol: c.symbol, secType: "", strike: null, right: null, expiry: null },
        side: "CANCELLED",
        quantity: c.totalQuantity,
        avgPrice: c.limitPrice,
        commission: null,
        realizedPNL: null,
        time: c.cancelledAt,
        exchange: "",
      });
    }
    return [...cancelRows, ...(orders?.executed_orders ?? [])];
  }, [cancelledOrders, orders?.executed_orders]);

  const execSortWithCancelled = useSort<ExecutedOrder, ExecOrderKey>(allExecutedRows, execOrderExtract, "time", "desc");

  // Group fills into position-level rows
  const positionGroups = useMemo(
    () => groupExecutedOrders(allExecutedRows, portfolio?.positions),
    [allExecutedRows, portfolio?.positions],
  );

  const extractExecSearch = useCallback(
    (g: PositionFillGroup) => `${g.symbol} ${g.description}`,
    [],
  );
  const execFilter = useTableFilter(positionGroups, extractExecSearch);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = useCallback((groupId: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  if (!orders) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <ClipboardList size={14} />
            Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body">
          <div className="alert-item">Waiting for orders data...</div>
        </div>
      </div>
    );
  }

  const canModify = (o: OpenOrder) => o.orderType === "LMT" || o.orderType === "STP LMT";
  const execCount = orders.executed_count + cancelledOrders.length;

  return (
    <>
      <CancelOrderDialog
        order={cancelTarget}
        loading={actionLoading}
        onConfirm={handleCancel}
        onClose={() => setCancelTarget(null)}
      />
      <ModifyOrderModal
        order={modifyTarget?.modalOrder ?? null}
        loading={actionLoading}
        prices={prices}
        portfolio={portfolio}
        onConfirm={handleModify}
        onClose={() => setModifyTarget(null)}
      />

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <ClipboardList size={14} />
            Open Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <TableSearch query={openFilter.query} setQuery={openFilter.setQuery} placeholder="Filter orders..." resultCount={openFilter.filtered.length} totalCount={openSort.sorted.length} />
            <span className="pill defined">{orders.open_count} ORDERS</span>
          </div>
        </div>
        <div className="section-body">
          {openOrderRows.length === 0 ? (
            <div className="alert-item">No open orders</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <SortTh<OpenOrderKey> label="Symbol" sortKey="symbol" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Action" sortKey="action" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Type" sortKey="orderType" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Quantity" sortKey="totalQuantity" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Limit Price" sortKey="limitPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Last Price" sortKey="lastPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Status" sortKey="status" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="TIF" sortKey="tif" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <th className="actions-th">Actions</th>
                </tr>
              </thead>
              <tbody>
                {openFilter.filtered.map((o) => {
                  if (o.kind === "combo") {
                    const comboCanModify = o.orders.every(canModify);
                    const comboModifyTarget = buildGroupedComboModifyTarget(o);
                    const isPendingCancel = o.orders.some((order) => pendingCancels.has(order.permId));
                    const isPendingModify = o.orders.some((order) => pendingModifies.has(order.permId));
                    const isPending = isPendingCancel || isPendingModify;

                    return (
                      <tr
                        key={o.id}
                        className={isPendingCancel
                          ? "row-pending-cancel"
                          : isPendingModify
                            ? "row-pending-modify"
                            : undefined}
                      >
                        <td>
                          <TickerLink ticker={o.symbol} />
                          <span
                            style={{
                              marginLeft: "8px",
                              fontFamily: "var(--font-mono)",
                              fontSize: "11px",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {o.summary}
                          </span>
                          {isPending && <Loader2 size={12} className="cancel-spinner" />}
                        </td>
                        <td>
                          <span className="pill neutral">COMBO</span>
                        </td>
                        <td>{o.structure}</td>
                        <td className="right">{o.totalQuantity}</td>
                        <td className="right">
                          <span className={isPendingModify ? "status-modifying" : ""}>
                            {isPendingModify ? "—" : o.limitPrice != null ? fmtPrice(o.limitPrice) : "—"}
                          </span>
                        </td>
                        <OrderPriceCell price={resolveOpenOrderComboPrice(o.orders, prices)} />
                        <td>
                          {isPendingCancel ? (
                            <span className="status-cancelling">Cancelling...</span>
                          ) : isPendingModify ? (
                            <span className="status-modifying">Modifying...</span>
                          ) : (
                            o.status
                          )}
                        </td>
                        <td>{o.tif}</td>
                        <td className="actions-cell">
                          {isPending ? (
                            <span className="cancel-pending-label">PENDING</span>
                          ) : (
                            <>
                              <button
                                className="btn-order-action btn-modify"
                                disabled={!comboCanModify}
                                title={comboCanModify ? "Modify combo order" : "Only LMT orders can be modified"}
                                onClick={() => setModifyTarget({
                                  modalOrder: comboModifyTarget.modalOrder,
                                  requestOrder: o.orders[0],
                                  cancelOrders: comboModifyTarget.cancelOrders,
                                })}
                              >
                                MODIFY
                              </button>
                              <button
                                className="btn-order-action btn-cancel"
                                onClick={() => void handleCancelCombo(o.orders)}
                              >
                                CANCEL ALL
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  }

                  const isPendingCancel = pendingCancels.has(o.order.permId);
                  const isPendingModify = pendingModifies.has(o.order.permId);
                  const isPending = isPendingCancel || isPendingModify;
                  return (
                    <tr
                      key={`${o.order.orderId}-${o.order.permId}`}
                      className={isPendingCancel
                        ? "row-pending-cancel"
                        : isPendingModify
                          ? "row-pending-modify"
                          : undefined}
                    >
                      <td>
                        <TickerLink ticker={o.order.contract.symbol} />
                        {o.summary ? (
                          <span
                            style={{
                              marginLeft: "8px",
                              fontFamily: "var(--font-mono)",
                              fontSize: "11px",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {o.summary}
                          </span>
                        ) : null}
                        {isPending && <Loader2 size={12} className="cancel-spinner" />}
                      </td>
                      <td>
                        <span className={`pill ${o.order.action === "BUY" ? "accum" : "distrib"}`}>
                          {o.order.action}
                        </span>
                      </td>
                      <td>{o.order.orderType}</td>
                      <td className="right">{o.order.totalQuantity}</td>
                      <td className="right">
                        {isPendingModify && o.order.orderType === "STP LMT" ? (
                          <span className="status-modifying">Modifying...</span>
                        ) : (
                          o.order.limitPrice != null ? fmtPrice(o.order.limitPrice) : "—"
                        )}
                      </td>
                      <OrderPriceCell price={resolveOrderLastPrice(o.order, prices, portfolio)} />
                      <td>
                        {isPendingCancel ? (
                          <span className="status-cancelling">Cancelling...</span>
                        ) : isPendingModify ? (
                          <span className="status-modifying">Modifying...</span>
                        ) : (
                          o.order.status
                        )}
                      </td>
                      <td>{o.order.tif}</td>
                      <td className="actions-cell">
                        {isPending ? (
                          <span className="cancel-pending-label">PENDING</span>
                        ) : (
                          <>
                            <button
                              className="btn-order-action btn-modify"
                              disabled={!canModify(o.order)}
                              title={canModify(o.order) ? "Modify limit price" : "Only LMT orders can be modified"}
                              onClick={() => setModifyTarget({
                                modalOrder: o.order,
                                requestOrder: o.order,
                              })}
                            >
                              MODIFY
                            </button>
                            <button
                              className="btn-order-action btn-cancel"
                              onClick={() => setCancelTarget(o.order)}
                            >
                              CANCEL
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <CheckCircle2 size={14} />
            Today&apos;s Executed Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Today's Executed Orders"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <TableSearch query={execFilter.query} setQuery={execFilter.setQuery} placeholder="Filter fills..." resultCount={execFilter.filtered.length} totalCount={positionGroups.length} />
            <span className="pill neutral">{positionGroups.length} {positionGroups.length === 1 ? "POSITION" : "POSITIONS"}</span>
          </div>
        </div>
        <div className="section-body">
          {positionGroups.length === 0 ? (
            <div className="alert-item">No fills this session</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: "24px" }}></th>
                  <th>Position</th>
                  <th>Action</th>
                  <th className="right">Quantity</th>
                  <th className="right">Net Price</th>
                  <th className="right">Commission</th>
                  <th className="right">Realized P&L</th>
                  <th>Time</th>
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {execFilter.filtered.map((group) => {
                  const isExpanded = expandedGroups.has(group.id);
                  const isCancelled = group.fills[0]?.side === "CANCELLED";
                  return (
                    <React.Fragment key={group.id}>
                      {/* Position group header row */}
                      <tr
                        className={`exec-group-header ${isCancelled ? "row-cancelled" : ""}`}
                        style={{ cursor: group.fills.length > 1 ? "pointer" : "default" }}
                        onClick={() => group.fills.length > 1 && toggleGroup(group.id)}
                      >
                        <td style={{ width: "24px", textAlign: "center" }}>
                          {group.fills.length > 1 && (
                            isExpanded
                              ? <ChevronDown size={14} style={{ color: "var(--text-secondary)" }} />
                              : <ChevronRight size={14} style={{ color: "var(--text-secondary)" }} />
                          )}
                        </td>
                        <td>
                          <TickerLink ticker={group.symbol} />
                          <span style={{ marginLeft: "8px", fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-secondary)" }}>
                            {group.description.replace(/^(Opened|Closed)\s+\w+\s*/, "")}
                          </span>
                          {isCancelled && <XCircle size={12} className="cancelled-icon" />}
                        </td>
                        <td>
                          <span className={`pill ${isCancelled ? "cancelled" : group.isClosing ? "distrib" : "accum"}`}>
                            {isCancelled ? "CANCELLED" : group.isClosing ? "CLOSE" : "OPEN"}
                          </span>
                        </td>
                        <td className="right">{group.totalQuantity}</td>
                        <td className="right">{group.netPrice != null ? fmtPrice(group.netPrice) : "—"}</td>
                        <td className="right">{group.totalCommission !== 0 ? fmtPrice(group.totalCommission) : "—"}</td>
                        <td className={`right ${group.totalPnL != null ? (group.totalPnL >= 0 ? "positive" : "negative") : ""}`}>
                          {group.totalPnL != null ? (() => {
                            // Return on Risk: P&L / entry notional. Entry = exit - P&L.
                            const optFills = group.fills.filter((f) => f.contract.secType === "OPT");
                            const exitNotional = optFills.reduce((sum, f) => sum + Math.abs((f.avgPrice ?? 0) * f.quantity * 100), 0);
                            const entryNotional = Math.abs(exitNotional - group.totalPnL);
                            const pct = entryNotional > 0 ? (group.totalPnL / entryNotional) * 100 : null;
                            return `${group.totalPnL >= 0 ? "+" : ""}${fmtPrice(group.totalPnL)}${pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)` : ""}`;
                          })() : "—"}
                        </td>
                        <td>{new Date(group.time).toLocaleTimeString()}</td>
                        <td>
                          {group.isClosing && group.totalPnL != null && (
                            <SharePnlButton data={positionGroupShareData(group, positionGroups, portfolio?.positions, portfolio?.trade_log_dates)} />
                          )}
                        </td>
                      </tr>
                      {/* Expanded fill detail rows */}
                      {isExpanded && group.fills.map((e, i) => {
                        const displaySide = e.side === "BOT" ? "BUY" : e.side === "SLD" ? "SELL" : e.side;
                        const isBAG = e.contract.secType === "BAG";
                        return (
                          <tr key={`${e.execId}-${i}`} className="exec-fill-row">
                            <td></td>
                            <td style={{ paddingLeft: "24px" }}>
                              <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-secondary)" }}>
                                {isBAG ? `${e.symbol}` : e.symbol}
                              </span>
                            </td>
                            <td>
                              <span className={`pill ${displaySide === "BUY" ? "accum" : "distrib"}`} style={{ fontSize: "9px" }}>
                                {displaySide}
                              </span>
                            </td>
                            <td className="right" style={{ color: "var(--text-secondary)" }}>{e.quantity}</td>
                            <td className="right" style={{ color: "var(--text-secondary)" }}>{e.avgPrice != null ? fmtPrice(e.avgPrice) : "—"}</td>
                            <td className="right" style={{ color: "var(--text-secondary)" }}>{e.commission != null && e.commission !== 0 ? fmtPrice(e.commission) : "—"}</td>
                            <td className="right" style={{ color: "var(--text-secondary)" }}>
                              {e.realizedPNL != null && Math.abs(e.realizedPNL) > 0.01
                                ? `${e.realizedPNL >= 0 ? "+" : ""}${fmtPrice(e.realizedPNL)}`
                                : "—"}
                            </td>
                            <td style={{ color: "var(--text-secondary)" }}>{new Date(e.time).toLocaleTimeString()}</td>
                            <td></td>
                          </tr>
                        );
                      })}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {orders.last_sync && (
        <div className="section">
          <div className="report-meta">
            Last Sync: {new Date(orders.last_sync).toLocaleString()} • Source: IB Gateway (4001)
          </div>
        </div>
      )}

      <HistoricalTradesSection />
    </>
  );
}

/* ─── Historical Trades (Flex Query) ───────────────────── */

const BLOTTER_PAGE_SIZE = 15;

type BlotterSortKey = "date" | "symbol" | "contract_desc" | "sec_type" | "status" | "net_quantity" | "total_commission" | "realized_pnl" | "cost_basis" | "proceeds";

function getTradeDate(item: BlotterTrade): string {
  if (item.executions.length === 0) return "";
  return item.executions[item.executions.length - 1].time;
}

const blotterExtract = (item: BlotterTrade, key: BlotterSortKey): string | number | null => {
  switch (key) {
    case "date": return getTradeDate(item);
    case "symbol": return item.symbol;
    case "contract_desc": return item.contract_desc;
    case "sec_type": return item.sec_type;
    case "status": return item.is_closed ? "Closed" : "Open";
    case "net_quantity": return item.total_quantity ?? item.net_quantity;
    case "total_commission": return item.total_commission;
    case "realized_pnl": return item.realized_pnl;
    case "cost_basis": return item.cost_basis;
    case "proceeds": return item.proceeds;
    default: return null;
  }
};

function HistoricalTradesSection() {
  const { data, loading, syncing, error, syncNow } = useBlotter(true);
  const [page, setPage] = useState(0);

  const allTrades = useMemo(() => {
    if (!data) return [];
    // Merge closed + open trades, sorted by most recent execution date desc
    const merged = [...(data.closed_trades ?? []), ...(data.open_trades ?? [])];
    merged.sort((a, b) => {
      const aDate = a.executions.length > 0 ? a.executions[a.executions.length - 1].time : "";
      const bDate = b.executions.length > 0 ? b.executions[b.executions.length - 1].time : "";
      return bDate.localeCompare(aDate);
    });
    return merged;
  }, [data]);

  const { sorted, sort, toggle } = useSort(allTrades, blotterExtract);

  const totalPages = Math.max(1, Math.ceil(sorted.length / BLOTTER_PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = sorted.slice(safePage * BLOTTER_PAGE_SIZE, (safePage + 1) * BLOTTER_PAGE_SIZE);

  // Reset page when data changes
  useEffect(() => { setPage(0); }, [data]);

  const totalCount = allTrades.length;
  const hasData = data && (data.as_of || totalCount > 0);

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">
          <ClipboardList size={14} />
          Historical Trades (30 Days)
          <InfoTooltip text={SECTION_TOOLTIPS["Historical Trades (30 Days)"]} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {data?.as_of && (
            <span className="report-meta" style={{ margin: 0, padding: 0, border: "none" }}>
              {new Date(data.as_of).toLocaleDateString()}
            </span>
          )}
          <span className="pill neutral">{totalCount} TRADES</span>
          <button
            className="sync-button"
            disabled={syncing}
            onClick={() => syncNow()}
          >
            {syncing ? <><Loader2 size={12} className="spin" /> Syncing...</> : "Refresh"}
          </button>
        </div>
      </div>
      <div className="section-body">
        {error && <div className="alert-item section-message bearish">{error}</div>}
        {loading && <div className="p-6"><TableSkeleton rows={5} columns={8} /></div>}
        {!loading && !hasData && !error && (
          <div className="alert-item section-message">No historical trades. Click REFRESH to fetch from IB.</div>
        )}
        {!loading && pageRows.length > 0 && (
          <>
            <table>
              <thead>
                <tr>
                  <SortTh<BlotterSortKey> label="Date" sortKey="date" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Symbol" sortKey="symbol" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Description" sortKey="contract_desc" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Type" sortKey="sec_type" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Side" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Qty" sortKey="net_quantity" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Commission" sortKey="total_commission" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Realized P&L" sortKey="realized_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Cost Basis" sortKey="cost_basis" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Proceeds" sortKey="proceeds" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((t, i) => (
                  <tr key={`${t.symbol}-${t.contract_desc}-${i}`}>
                    <td>{getTradeDate(t) ? new Date(getTradeDate(t)).toLocaleDateString() : "—"}</td>
                    <td><TickerLink ticker={t.symbol} /></td>
                    <td>{t.contract_desc}</td>
                    <td>{t.sec_type}</td>
                    <td>
                      <span className={`pill ${t.is_closed ? "neutral" : "defined"}`}>
                        {t.is_closed ? "Closed" : "Open"}
                      </span>
                    </td>
                    <td className="right">{t.total_quantity ?? t.net_quantity}</td>
                    <td className="right">{t.total_commission != null ? fmtPrice(t.total_commission) : "---"}</td>
                    <td className={`right ${(t.realized_pnl ?? 0) >= 0 ? "positive" : "negative"}`}>
                      {t.realized_pnl != null ? (
                        <>
                          {t.realized_pnl >= 0 ? "+" : ""}{fmtPrice(t.realized_pnl)}
                          {t.cost_basis != null && Math.abs(t.cost_basis) > 0 ? ` (${((t.realized_pnl / Math.abs(t.cost_basis)) * 100) >= 0 ? "+" : ""}${((t.realized_pnl / Math.abs(t.cost_basis)) * 100).toFixed(1)}%)` : ""}
                        </>
                      ) : "---"}
                    </td>
                    <td className="right">{t.cost_basis != null ? fmtPrice(t.cost_basis) : "---"}</td>
                    <td className="right">{t.proceeds != null ? fmtPrice(t.proceeds) : "---"}</td>
                    <td>
                      {t.is_closed && <SharePnlButton data={blotterShareData(t)} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {totalPages > 1 && (
              <div className="pagination">
                <button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
                  &larr; Prev
                </button>
                <span className="page-info">Page {safePage + 1} of {totalPages}</span>
                <button disabled={safePage >= totalPages - 1} onClick={() => setPage(safePage + 1)}>
                  Next &rarr;
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Root switch ───────────────────────────────────────── */

type WorkspaceSectionsProps = {
  section: WorkspaceSection;
  portfolio?: PortfolioData | null;
  portfolioLastSync?: string | null;
  orders?: OrdersData | null;
  prices?: Record<string, PriceData>;
  tickerParam?: string;
  theme?: "dark" | "light";
  marketState?: MarketState;
};

export default function WorkspaceSections({ section, portfolio, portfolioLastSync, orders, prices, tickerParam, theme, marketState }: WorkspaceSectionsProps) {
  switch (section) {
    case "dashboard":
      return null;
    case "flow-analysis":
      return <FlowSections />;
    case "portfolio":
      return <PortfolioSections portfolio={portfolio ?? null} prices={prices} />;
    case "performance":
      return <PerformancePanel portfolioLastSync={portfolioLastSync} marketState={marketState} />;
    case "orders":
      return <OrdersSections orders={orders ?? null} prices={prices} portfolio={portfolio} />;
    case "scanner":
      return <ScannerSections />;
    case "discover":
      return <DiscoverSections />;
    case "journal":
      return <JournalSections />;
    case "regime":
      return <RegimePanel prices={prices ?? {}} marketState={marketState} />;
    case "cta":
      return <CtaPage />;
    case "ticker-detail":
      return tickerParam ? (
        <TickerWorkspace ticker={tickerParam} theme={theme ?? "dark"} />
      ) : null;
    default:
      return <FlowSections />;
  }
}
