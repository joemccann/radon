"use client";

import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ClipboardList,
  ArrowDown,
  ArrowUp,
  History,
  Inbox,
  Loader2,
  Search,
  Sparkles,
  TrendingDown,
  TriangleAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import { ScannerModeTabs } from "./ScannerModeTabs";
import { SigMeter } from "./SigMeter";
import SectionEmptyState from "./SectionEmptyState";
import type { BlotterTrade, DiscoverCandidate, ExecutedOrder, FlowAnalysisPosition, OpenOrder, OrdersData, PortfolioData, PortfolioPosition, ScannerSignal, TradeEntry, WorkspaceSection } from "@/lib/types";
import { useOrderActions } from "@/lib/OrderActionsContext";
import type { DepthBook, PriceData, Trade } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { useJournal } from "@/lib/useJournal";
import {
  filterTradesByRange,
  isClosedTrade,
  rangeForPreset,
  summarizeRangePnl,
  type JournalRangePreset,
} from "@/lib/journal/rangePnl";
import { useDiscover } from "@/lib/useDiscover";
import { useFlowAnalysis } from "@/lib/useFlowAnalysis";
import { useScanner } from "@/lib/useScanner";
import { useThetaHarvester } from "@/lib/useThetaHarvester";
import { useStrengthConfirmation } from "@/lib/useStrengthConfirmation";
import { useLeap } from "@/lib/useLeap";
import { useGarchConvergence } from "@/lib/useGarchConvergence";
import { useVolCone } from "@/lib/useVolCone";
import { useBlotter } from "@/lib/useBlotter";
import { formatTradeDate } from "@/lib/blotter/formatTradeDate";
import { isEarlierLocalDay } from "@/lib/holdTime";
import CashFlowsSection from "@/components/CashFlowsSection";
import { useSort } from "@/lib/useSort";
import { useTableFilter } from "@/lib/useTableFilter";
import TableSearch from "./TableSearch";
import SortTh from "./SortTh";
import { usePriceDirection } from "@/lib/usePriceDirection";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import {
  buildOpenOrderDisplayRows,
  type OpenOrderDisplayRow,
  buildExecutedGroupDescription,
  resolveOpenOrderComboPrice,
  findPortfolioLegDirection,
} from "@/lib/openOrderCombos";
import {
  distanceToFill,
  formatDistanceDelta,
  formatFillQuantity,
  isPartialFill,
  mapOrderStatus,
  resolveOrderIntent,
  statusPillClass,
  summarizeOpenOrderRows,
} from "@/lib/orders/orderDisplay";
import {
  classifyDisplayRowSession,
  isExtendedFillLive,
  summarizeSessionWindows,
} from "@/lib/orders/sessionWindow";
import SessionWindowChip from "./orders/SessionWindowChip";
import {
  filterExecutedToEtToday,
  formatExecutedFillTime,
} from "@/lib/orders/executedToday";
import {
  OPEN_ORDERS_DENSITY_KEY,
  HISTORICAL_PAGE_SIZE_KEY,
  HISTORICAL_PAGE_SIZES,
  canModifyDisplayRow,
  flattenSelectedOpenOrders,
  formatShowingRange,
  isEditableKeyboardTarget,
  openOrderRowKey,
  parseHistoricalPageSize,
  parseOpenOrdersDensity,
  resolveOrdersShortcut,
  setAllSelectionKeys,
  toggleSelectionKey,
  type HistoricalPageSize,
  type OpenOrdersDensity,
} from "@/lib/orders/ordersUx";
import { formatRelativeTime } from "@/lib/adminFormat";
import { computeLegImpliedValue, computeOrderImpliedValue } from "@/lib/impliedValue";
import { useRiskFreeRate } from "@/lib/useRiskFreeRate";
import { useColumnVisibility } from "@/lib/useColumnVisibility";
import { useViewport } from "@/lib/useViewport";
import { ColumnsToggle, type ColumnsToggleEntry } from "./ColumnsToggle";
import MobileOrderList from "./mobile/MobileOrderList";
import MobileBlotterList from "./mobile/MobileBlotterList";
import MobileExecutedList from "./mobile/MobileExecutedList";
import MobileJournalList from "./mobile/MobileJournalList";
import SignalCard from "./mobile/SignalCard";
import MobileFlowSparkline from "./mobile/MobileFlowSparkline";
import { buildGroupedComboModifyTarget } from "@/lib/openOrderComboModify";
import SpectralLoader from "./SpectralLoader";
import CancelOrderDialog from "./CancelOrderDialog";
import ModifyOrderModal from "./ModifyOrderModal";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import RegimePanel from "./RegimePanel";
import CtaPage from "./CtaPage";
import AdminWorkspace from "./admin/AdminWorkspace";
import PreferencesSection from "./PreferencesSection";
import ProfileContent from "./profile/ProfileContent";
import WatchlistContent from "./watchlist/WatchlistContent";
import PerformancePanel from "./PerformancePanel";
import OptionsWorkspacePanel from "./OptionsWorkspacePanel";
import InfoTooltip from "./InfoTooltip";
import SharePnlButton, { type SharePnlData } from "./SharePnlButton";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import TickerLink from "./TickerLink";
import TickerWorkspace from "./TickerWorkspace";
import TickerFlowReport from "./flow-analysis/TickerFlowReport";
import ThetaHarvesterScanner, { type ThetaScanParams } from "./ThetaHarvesterScanner";
import StrengthConfirmationScanner from "./StrengthConfirmationScanner";
import LeapScanner from "./LeapScanner";
import GarchConvergenceScanner from "./GarchConvergenceScanner";
import VolConePanel from "./VolConePanel";
import FlowAnalysisTickerInput from "./flow-analysis/FlowAnalysisTickerInput";
import { InformedFlowPanel } from "./flow-analysis/InformedFlowPanel";
import { AlertsPanel } from "./alerts/AlertsPanel";
import WorkflowComposer from "@/app/workflow/WorkflowComposer";
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
    // NOT `?? 0` — see SharePnlData.pnl. R-249.
    pnl: e.realizedPNL ?? null,
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

/** Earliest per-contract opening-fill date across a group's option legs, from
 *  the `contract_open_dates` map (keyed `SYMBOL|EXPIRY|RIGHT|STRIKE`, the
 *  conId-free form the journal can key on). The earliest fill of any contract
 *  is by definition its opening fill, so this is the position's true entry day
 *  even after it is fully closed and gone from the portfolio. Null when no leg
 *  has a known open date. */
function groupEarliestOpenDate(
  group: PositionFillGroup,
  contractOpenDates: Record<string, string> | undefined,
): string | null {
  if (!contractOpenDates) return null;
  let earliest: string | null = null;
  for (const fill of group.fills) {
    if (fill.contract.secType !== "OPT") continue;
    const symbol = fill.contract.symbol?.toUpperCase();
    const expiry = fill.contract.expiry?.replace(/-/g, "");
    const strike = fill.contract.strike;
    const rightRaw = fill.contract.right;
    const right = rightRaw === "CALL" ? "C" : rightRaw === "PUT" ? "P" : rightRaw;
    if (!symbol || !expiry || !right || strike == null) continue;
    const opened = contractOpenDates[`${symbol}|${expiry}|${right}|${strike}`];
    if (opened && (earliest == null || opened < earliest)) earliest = opened;
  }
  return earliest;
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

/** Net cash received by a group's closing fills in dollars
 *  (SLD positive, BOT negative). Options use the ×100 multiplier, stocks ×1.
 *  Null when any fill is unpriced or sideless. */
function closedGroupCloseCash(group: PositionFillGroup): number | null {
  let priced = group.fills.filter(
    (f) => f.contract.secType === "OPT" || f.contract.secType === "STK",
  );
  // REL-219 (R-582): a mixed group (same-day opening BUYs + a partial close)
  // must sum CLOSING cash only, or the P&L identity below derives an entry
  // basis from mixed open+close cash and the return % is fabricated. The
  // realizedPNL-bearing fills are exactly the ones group.totalPnL came from.
  const closing = priced.filter(
    (f) => f.realizedPNL != null && Math.abs(f.realizedPNL) > 0.01,
  );
  if (closing.length > 0 && closing.length < priced.length) priced = closing;
  if (priced.length === 0) return null;
  let closeCash = 0;
  for (const fill of priced) {
    if (fill.avgPrice == null || !Number.isFinite(fill.avgPrice)) return null;
    const cashSign = fill.side === "SLD" || fill.side === "SELL"
      ? 1
      : fill.side === "BOT" || fill.side === "BUY"
        ? -1
        : 0;
    if (cashSign === 0) return null;
    const multiplier = fill.contract.secType === "OPT" ? 100 : 1;
    closeCash += cashSign * fill.avgPrice * Math.abs(fill.quantity) * multiplier;
  }
  return closeCash;
}

/** Entry cash implied by the realized-P&L identity openCash = pnl − closeCash,
 *  in dollars (credit positive, debit negative). Sign-correct for both long
 *  (debit) and short (credit) entries — a buy-to-close ADDS the cover cost to
 *  the basis instead of subtracting it. */
function closedGroupOpenCash(group: PositionFillGroup): number | null {
  if (group.totalPnL == null) return null;
  const closeCash = closedGroupCloseCash(group);
  if (closeCash == null) return null;
  const openCash = group.totalPnL - closeCash;
  return Math.abs(openCash) < 0.01 ? null : openCash;
}

/** Return on risk % for a closed fill group: realized P&L over the entry basis
 *  implied by the P&L identity. Shared by the Executed Orders table cell and
 *  the share-card fallback so both surfaces always agree. */
export function closedGroupReturnPct(group: PositionFillGroup): number | null {
  const openCash = closedGroupOpenCash(group);
  if (openCash == null || group.totalPnL == null) return null;
  return (group.totalPnL / Math.abs(openCash)) * 100;
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
  contractOpenDates?: Record<string, string>,
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
      const closeStrikes = new Set(
        group.fills
          .filter((f) => f.contract.secType === "OPT")
          .map((f) => f.contract.strike)
          .filter((s): s is number => s != null),
      );
      const matchingPosition = portfolioPositions.find((p) => {
        if (p.ticker !== group.symbol) return false;
        // Every strike in the closed group must exist on the candidate's legs —
        // word overlap alone matched a closed MU $1000 Call to a live $1050 Call.
        if (closeStrikes.size > 0) {
          const legStrikes = new Set(p.legs.map((l) => l.strike).filter((s): s is number => s != null));
          if (![...closeStrikes].every((strike) => legStrikes.has(strike))) return false;
        }
        const posWords = p.structure.replace(/[()$,]/g, " ").toLowerCase().split(/\s+/).filter(Boolean);
        // At least 2 key words must overlap (e.g., "long" + "call", or "bull" + "spread")
        const overlap = posWords.filter((w) => descWords.includes(w));
        return overlap.length >= 2;
      });
      if (matchingPosition) {
        // PortfolioLeg.avg_cost is per-contract for options (already × 100) and per-share for stocks.
        // entryPrice + entryNotional in this function follow the per-share convention,
        // so divide by the leg's multiplier when constructing entryPrice from avg_cost.
        const legMultiplier = (leg: typeof matchingPosition.legs[number]) => (leg.type === "Stock" ? 1 : 100);
        if (matchingPosition.legs.length === 1) {
          const onlyLeg = matchingPosition.legs[0];
          entryPrice = onlyLeg.avg_cost / legMultiplier(onlyLeg);
        } else if (matchingPosition.legs.length > 1 && matchingPosition.contracts > 0) {
          // Net entry price for combo = sum of (direction-adjusted per-share avg_cost per leg)
          const netCost = matchingPosition.legs.reduce((sum, leg) => {
            const sign = leg.direction === "LONG" ? -1 : 1; // Long = paid, Short = received
            return sum + sign * (leg.avg_cost / legMultiplier(leg));
          }, 0);
          entryPrice = netCost;
        }
        // Use entry_date from portfolio (date only, no time)
        if (matchingPosition.entry_date) {
          entryTime = matchingPosition.entry_date;
        }
        // Calculate notional for P&L %
        if (entryNotional === 0 && entryPrice != null) {
          const positionMultiplier = matchingPosition.legs.some((leg) => leg.type !== "Stock") ? 100 : 1;
          entryNotional = Math.abs(entryPrice) * (matchingPosition.contracts || group.totalQuantity) * positionMultiplier;
        }
      }
    }

    // Fallback for fully-closed positions no longer in portfolio: derive the
    // entry basis from the realized-P&L identity (openCash = pnl − closeCash),
    // which is sign-correct for both debit (long) and credit (short) entries.
    if (entryPrice == null) {
      const openCash = closedGroupOpenCash(group);
      if (openCash != null) {
        const comboUnits = Math.max(group.totalQuantity, 1);
        // Per-group multiplier: ×100 only when the group carries an option
        // (OPT/BAG) fill; a stock-only group's basis is already per share.
        const groupMultiplier = group.fills.some(
          (f) => f.contract.secType === "OPT" || f.contract.secType === "BAG",
        ) ? 100 : 1;
        entryPrice = -(openCash / groupMultiplier) / comboUnits;
        if (entryNotional === 0) {
          entryNotional = Math.abs(openCash);
        }
      }
    }

    if (entryNotional > 0) {
      pnlPct = (group.totalPnL / entryNotional) * 100;
    } else {
      pnlPct = closedGroupReturnPct(group);
    }
  }

  // Exit time is the closing group's time
  const exitTime = group.isClosing ? group.time : null;

  // Per-contract opening-fill date: the last honest entry source for a
  // position opened days ago and fully closed today. It is gone from the
  // portfolio, its opening fills are outside the executed-orders lookback, and
  // the per-ticker trade_log_dates map below carries only the close date. The
  // realized-P&L identity above recovers the entry PRICE but no time, so
  // without this the share card silently loses its hold duration. Guard so a
  // stale/bad open date can never post-date the exit.
  if (entryTime == null && contractOpenDates) {
    const opened = groupEarliestOpenDate(group, contractOpenDates);
    if (opened && (exitTime == null || isEarlierLocalDay(opened, exitTime))) {
      entryTime = opened;
    }
  }

  // Fallback entry time from trade_log for fully-closed positions. The map
  // holds each ticker's LATEST journal date, so once the closing fill is
  // journaled the value is the close date itself — only a date from an
  // earlier day can be a real entry.
  if (entryTime == null && tradeLogDates?.[group.symbol]) {
    const candidate = tradeLogDates[group.symbol];
    if (exitTime == null || isEarlierLocalDay(candidate, exitTime)) {
      entryTime = candidate;
    }
  }

  return {
    description: group.description,
    pnl: group.totalPnL ?? null,
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

export function groupExecutedOrders(
  fills: ExecutedOrder[],
  portfolioPositions?: readonly PortfolioPosition[],
): PositionFillGroup[] {
  if (fills.length === 0) return [];

  // Separate cancelled orders (keep as-is, ungrouped)
  const cancelled = fills.filter((f) => f.side === "CANCELLED");
  const real = fills.filter((f) => f.side !== "CANCELLED");

  // ── Close detection ────────────────────────────────────────────────────
  // IB's commission report (which carries realizedPNL) arrives async,
  // sometimes seconds after the execution event. When it hasn't landed yet
  // realizedPNL is null on the fill, and the naive "realizedPNL > 0" check
  // mis-classifies a buy-to-close on a short put as opening a new long put.
  // Fall back to portfolio context: if the fill direction opposes an existing
  // leg, the trade reduces (closes) that leg.
  const fillNormalizedRight = (
    fill: ExecutedOrder,
  ): "C" | "P" | null => {
    const r = fill.contract.right;
    if (r === "C" || r === "CALL") return "C";
    if (r === "P" || r === "PUT") return "P";
    return null;
  };

  const portfolioLegBasisFor = (
    fill: ExecutedOrder,
  ): { direction: "LONG" | "SHORT"; avgCost: number } | null => {
    if (fill.contract.secType !== "OPT") return null;
    const right = fillNormalizedRight(fill);
    if (!right || fill.contract.expiry == null || fill.contract.strike == null) return null;
    const dir = findPortfolioLegDirection(
      portfolioPositions,
      fill.contract.symbol,
      fill.contract.expiry,
      fill.contract.strike,
      right,
    );
    if (!dir) return null;
    if (!portfolioPositions) return null;
    const targetExpiry = fill.contract.expiry?.replace(/-/g, "") ?? "";
    const target = portfolioPositions.find(
      (p) => p.ticker.toUpperCase() === fill.contract.symbol.toUpperCase()
        && p.expiry.replace(/-/g, "") === targetExpiry,
    );
    const leg = target?.legs.find(
      (l) => l.type === (right === "C" ? "Call" : "Put") && l.strike === fill.contract.strike,
    );
    return { direction: dir, avgCost: leg?.avg_cost != null ? Math.abs(leg.avg_cost) : 0 };
  };

  const isClosingFill = (fill: ExecutedOrder): boolean => {
    // Primary signal: IB populated realizedPNL on the commission report.
    // Applies to every sec type — a stock (or future) SELL-to-close / BUY-to-cover
    // carries realizedPNL just as an option close does.
    if (fill.realizedPNL != null && Math.abs(fill.realizedPNL) > 0.01) return true;
    // Fallback (options only): this fill closes against an existing portfolio leg.
    if (fill.contract.secType !== "OPT") return false;
    // BOT against a SHORT leg, or SLD against a LONG leg = reduces the position.
    const basis = portfolioLegBasisFor(fill);
    if (!basis) return false;
    if ((fill.side === "BOT" || fill.side === "BUY") && basis.direction === "SHORT") return true;
    if ((fill.side === "SLD" || fill.side === "SELL") && basis.direction === "LONG") return true;
    return false;
  };

  // For a detected close where IB hasn't returned realizedPNL yet, compute
  // P&L from the portfolio leg's avg_cost (per-contract, already × multiplier
  // per IB convention). Returns null when no basis is available (fully-closed
  // position whose original open was in a prior session).
  const fallbackPnlFor = (fill: ExecutedOrder): number | null => {
    if (fill.realizedPNL != null && Math.abs(fill.realizedPNL) > 0.01) return null;
    const basis = portfolioLegBasisFor(fill);
    if (!basis || basis.avgCost <= 0) return null;
    if (fill.avgPrice == null || !Number.isFinite(fill.avgPrice)) return null;
    const closePerContract = fill.avgPrice * 100;
    const qty = Math.abs(fill.quantity);
    if (basis.direction === "LONG") {
      // Closed long: profit when close price > entry premium
      return (closePerContract - basis.avgCost) * qty;
    }
    // Closed short: profit when entry premium > close price (paid less to buy back)
    return (basis.avgCost - closePerContract) * qty;
  };

  type MinuteBucket = {
    symbol: string;
    correlation: string | null;
    latestMs: number | null;
    opens: ExecutedOrder[];
    closes: ExecutedOrder[];
    bags: ExecutedOrder[];
  };

  const buckets: MinuteBucket[] = [];
  const durableCorrelation = (fill: ExecutedOrder): string | null => {
    if (typeof fill.orderRef === "string" && fill.orderRef.trim()) return `ref:${fill.orderRef.trim()}`;
    if (typeof fill.permId === "number" && Number.isFinite(fill.permId) && fill.permId > 0) return `perm:${fill.permId}`;
    if (typeof fill.orderId === "number" && Number.isFinite(fill.orderId) && fill.orderId > 0) return `order:${fill.orderId}`;
    return null;
  };
  const sortedReal = [...real].sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  for (const fill of sortedReal) {
    const sym = fill.contract.symbol;
    const correlation = durableCorrelation(fill);
    const fillMs = Date.parse(fill.time);
    let target = correlation
      ? buckets.find((bucket) => bucket.symbol === sym && bucket.correlation === correlation)
      : [...buckets].reverse().find((bucket) => (
          bucket.symbol === sym
          && bucket.correlation === null
          && bucket.latestMs != null
          && Number.isFinite(fillMs)
          && Math.abs(fillMs - bucket.latestMs) <= 60_000
        ));
    if (!target) {
      target = {
        symbol: sym,
        correlation,
        latestMs: Number.isFinite(fillMs) ? fillMs : null,
        opens: [],
        closes: [],
        bags: [],
      };
      buckets.push(target);
    }
    if (Number.isFinite(fillMs)) target.latestMs = Math.max(target.latestMs ?? fillMs, fillMs);

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
    if (bagFills.length > 0) {
      const complete = bagFills.every((fill) => (
        fill.avgPrice != null && Number.isFinite(fill.avgPrice) && Number.isFinite(fill.quantity) && fill.quantity > 0
      ));
      if (complete) {
        const bagQty = bagFills.reduce((sum, fill) => sum + fill.quantity, 0);
        netPrice = bagQty > 0
          ? Number((bagFills.reduce((sum, fill) => sum + fill.avgPrice! * fill.quantity, 0) / bagQty).toFixed(4))
          : null;
      }
    } else if (optFills.length > 0) {
      const totalQty = optFills.reduce((s, f) => s + f.quantity, 0);
      const weightedSum = optFills.reduce((s, f) => s + (f.avgPrice ?? 0) * f.quantity, 0);
      netPrice = totalQty > 0 ? Number((weightedSum / totalQty).toFixed(4)) : null;
    }

    const totalCommission = optFills.reduce((sum, f) => sum + (f.commission ?? 0), 0);
    let totalPnL: number | null = null;
    if (isClosing) {
      totalPnL = optFills.reduce((sum, f) => {
        if (f.realizedPNL != null && Math.abs(f.realizedPNL) > 0.01) return sum + f.realizedPNL;
        // realizedPNL not delivered (commission report still in flight or
        // session restart lost it). Fall back to portfolio basis for the leg.
        const fallback = fallbackPnlFor(f);
        return sum + (fallback ?? 0);
      }, 0);
      // If every fill in the group failed both signals, surface null instead
      // of a misleading $0.
      const anySignal = optFills.some(
        (f) => (f.realizedPNL != null && Math.abs(f.realizedPNL) > 0.01) || fallbackPnlFor(f) != null,
      );
      if (!anySignal) totalPnL = null;
    }

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
  for (const bucket of buckets) {
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
    } else if (bags.length > 0) {
      result.push({ ...makeGroup(bags, false), id: `${nextId()}-open` });
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
  const realizedPnl = t.realized_pnl ?? null;
  const realizedBasisRaw = t.realized_cost_basis ?? t.cost_basis;
  const realizedBasis = realizedBasisRaw != null ? Math.abs(realizedBasisRaw) : 0;
  const pnlPct = realizedPnl != null && realizedBasis > 0
    ? (realizedPnl / realizedBasis) * 100
    : null;
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
    pnl: realizedPnl,
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

function flowLabelTone(flowClass: string): "pos" | "neg" | "warn" | "mut" {
  if (flowClass === "accum" || flowClass === "bullish") return "pos";
  if (flowClass === "distrib" || flowClass === "bearish") return "neg";
  if (flowClass === "lean-bullish" || flowClass === "lean-bearish") return "warn";
  return "mut";
}

function FlowMobileCards({ rows }: { rows: FlowAnalysisPosition[] }) {
  const { sorted } = useSort(rows, flowPosExtract);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} data-testid="mobile-flow-list">
      {sorted.map((item) => {
        const buyPct = item.buy_ratio != null ? Math.round(item.buy_ratio * 100) : null;
        return (
          <SignalCard
            key={`${item.ticker}-${item.position}`}
            ticker={item.ticker}
            score={Math.min(100, Math.max(0, Math.round(item.strength)))}
            signals={[
              {
                label: item.flow_label,
                tone: flowLabelTone(item.flow_class),
              },
            ]}
            stats={[
              {
                label: "Position",
                value: item.position,
              },
              {
                label: "Buy Ratio",
                value: buyPct != null ? `${buyPct}%` : "---",
              },
              {
                label: "Strength",
                value: String(item.strength),
              },
            ]}
            footer={
              item.daily_buy_ratios?.length ? (
                <MobileFlowSparkline ratios={item.daily_buy_ratios} />
              ) : undefined
            }
          />
        );
      })}
    </div>
  );
}

function ResponsiveFlowTable({ rows, lastColumn }: { rows: FlowAnalysisPosition[]; lastColumn: string }) {
  const { isMobile, hasMounted } = useViewport();
  if (hasMounted && isMobile) {
    return <FlowMobileCards rows={rows} />;
  }
  return (
    <div className="table-wrap">
      <FlowTable rows={rows} lastColumn={lastColumn} />
    </div>
  );
}

function FlowSections({ tickerParam }: { tickerParam?: string }) {
  if (tickerParam) {
    return (
      <>
        <FlowAnalysisTickerInput initialTicker={tickerParam} />
        <TickerFlowReport ticker={tickerParam} />
        <InformedFlowPanel ticker={tickerParam} />
      </>
    );
  }
  return (
    <>
      <FlowAnalysisTickerInput />
      <FlowSectionsBody />
    </>
  );
}

type FlowSegment = "supports" | "against" | "watch" | "neutral";

function FlowSectionsBody() {
  const { data, syncing, error, lastSync } = useFlowAnalysis(true);
  const { isMobile, hasMounted } = useViewport();
  const [activeSegment, setActiveSegment] = useState<FlowSegment>("supports");

  const supportsArr = data?.supports ?? [];
  const againstArr = data?.against ?? [];
  const watchArr = data?.watch ?? [];
  const neutralArr = data?.neutral ?? [];
  const totalScanned = data?.positions_scanned ?? 0;
  // "5 Trading Days" was a literal in the JSX beside two payload-derived
  // numbers, so it carried their authority while consulting nothing. The
  // producer's real window is whatever `daily_buy_ratios` covers — a scan
  // that got short upstream data covers fewer. UI Copy Rules forbid the
  // hardcoded form for exactly this reason. R-269.
  const darkPoolSessions = useMemo(() => {
    const dates = new Set<string>();
    for (const bucket of [data?.supports, data?.against, data?.watch, data?.neutral]) {
      for (const position of bucket ?? []) {
        for (const day of position.daily_buy_ratios ?? []) {
          if (day.date) dates.add(day.date);
        }
      }
    }
    return dates.size;
  }, [data]);

  const actionItems = againstArr.filter((p) => p.strength >= 15);

  if (hasMounted && isMobile) {
    const segmentRows: Record<FlowSegment, FlowAnalysisPosition[]> = {
      supports: supportsArr,
      against: againstArr,
      watch: watchArr,
      neutral: neutralArr,
    };
    const segmentLabels: { key: FlowSegment; label: string }[] = [
      { key: "supports", label: "Supports" },
      { key: "against", label: "Against" },
      { key: "watch", label: "Watch" },
      { key: "neutral", label: "Neutral" },
    ];

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {error && (
          <div style={{ padding: "8px 16px" }}>
            <div className="alert-item bearish">{error}</div>
          </div>
        )}
        {actionItems.length > 0 && (
          <div style={{ margin: "8px 16px" }}>
            <div className="alert-box">
              <div className="alert-title">
                <TriangleAlert size={14} />
                ACTION ITEMS
              </div>
              {actionItems.map((item) => (
                <div key={`${item.ticker}-${item.position}`} className="alert-item">
                  <span className="alert-ticker">{item.ticker}</span>, {item.position}: {item.note}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Segment control */}
        <div className="m-segment" role="tablist" aria-label="Flow sections">
          {segmentLabels.map(({ key, label }) => (
            <button
              key={key}
              role="tab"
              aria-selected={activeSegment === key}
              className={`m-segment__item${activeSegment === key ? " m-segment__item--active" : ""}`}
              onClick={() => setActiveSegment(key)}
              type="button"
            >
              {label}
              {segmentRows[key].length > 0 && (
                <span
                  style={{
                    marginLeft: 4,
                    fontSize: 10,
                    fontFamily: "var(--font-mono)",
                    opacity: 0.7,
                  }}
                >
                  {segmentRows[key].length}
                </span>
              )}
            </button>
          ))}
        </div>

        <div style={{ padding: "12px 16px" }}>
          {segmentRows[activeSegment].length > 0 ? (
            <FlowMobileCards rows={segmentRows[activeSegment]} />
          ) : (
            <div className="alert-item" style={{ textAlign: "center", padding: "24px 0" }}>
              {syncing ? "Scanning portfolio flow..." : `No ${activeSegment} positions`}
            </div>
          )}
        </div>

        {lastSync && (
          <div className="report-meta" style={{ padding: "0 16px 12px", margin: 0 }}>
            {new Date(lastSync).toLocaleTimeString()} · {totalScanned} positions
          </div>
        )}
      </div>
    );
  }

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
                <span className="alert-ticker">{item.ticker}</span>, {item.position}: {item.note}
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
            <ResponsiveFlowTable rows={supportsArr} lastColumn="Signal" />
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
            <ResponsiveFlowTable rows={againstArr} lastColumn="Concern" />
          ) : (
            <SectionEmptyState icon={TrendingDown} headline="No contradicting flow detected" />
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
            <ResponsiveFlowTable rows={neutralArr} lastColumn="Note" />
          ) : (
            <SectionEmptyState icon={Circle} headline="No neutral positions" />
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
            <ResponsiveFlowTable rows={watchArr} lastColumn="Note" />
          ) : (
            <SectionEmptyState icon={Bell} headline="No watch items" />
          )}
        </div>
      </div>

      <div className="section">
        <div className="report-meta">
          {lastSync
            ? `Report Generated: ${new Date(lastSync).toLocaleString()} • Source: UW API • Dark Pool Lookback: ${darkPoolSessions > 0 ? `${darkPoolSessions} Trading Day${darkPoolSessions === 1 ? "" : "s"}` : "unavailable"} • ${totalScanned} Positions Scanned`
            : "Awaiting initial flow analysis..."}
        </div>
      </div>
    </>
  );
}

/* ─── Scanner table ─────────────────────────────────────── */

type ScannerSortKey = "ticker" | "signal" | "direction" | "score" | "strength" | "buy_ratio" | "sustained_days" | "num_prints";
type ScannerMode = "flow" | "discover" | "theta" | "strength" | "leap" | "garch" | "vol-cone";

const SCANNER_HEADER_HELP = {
  signal: "Flow intensity bucket from dark-pool activity. STRONG means the flow score is high enough to review immediately.",
  direction: "Dominant institutional flow direction. ACCUMULATION leans bullish; DISTRIBUTION leans bearish.",
  score: "Composite flow score across strength, buy ratio, sustained activity, and print count.",
  strength: "Raw dark-pool flow strength for the ticker. Higher values indicate more forceful institutional activity.",
  "buy-ratio": "Share of prints classified as buyer-initiated. Higher ratios support accumulation; lower ratios support distribution.",
  sustained: "Number of sessions the signal has persisted. Longer streaks carry more weight than one-day prints.",
  prints: "Number of dark-pool transactions in the scan window. More prints improve confidence in the signal.",
} as const;

type ScannerHelpKey = keyof typeof SCANNER_HEADER_HELP;

function scannerHelpProps(label: string, helpKey: ScannerHelpKey) {
  return {
    helpText: SCANNER_HEADER_HELP[helpKey],
    helpAriaLabel: `${label} scanner signal details`,
    helpTriggerTestId: `scanner-header-tooltip-${helpKey}`,
    helpContentTestId: `scanner-header-tooltip-content-${helpKey}`,
  };
}

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

function scannerSignalTone(signal: string): "pos" | "warn" | "neg" {
  if (signal === "STRONG") return "pos";
  if (signal === "MODERATE") return "warn";
  return "neg";
}

function scannerDirTone(dir: string): "pos" | "neg" | "mut" {
  if (dir === "ACCUMULATION") return "pos";
  if (dir === "DISTRIBUTION") return "neg";
  return "mut";
}

/**
 * Open a flow row's ticker on the chain deck. Dark-pool prints carry no
 * contract, so this is a ticker-level link: `?deck=c` lands on the chain with
 * its default ATM window and an empty builder. Null when there is nothing to
 * trade — a blank ticker, or a row the scanner itself could not read.
 */
export function flowOrderHref(row: ScannerSignal): string | null {
  const ticker = row.ticker.trim();
  if (!ticker) return null;
  if (row.signal === "NONE" || row.signal === "ERROR") return null;
  const params = new URLSearchParams({ deck: "c", src: "flow" });
  return `/${encodeURIComponent(ticker.toUpperCase())}?${params.toString()}`;
}

function ScannerSections({ defaultMode }: { defaultMode?: ScannerMode } = {}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryModeParam = searchParams.get("mode");
  const parsedQueryMode: ScannerMode = queryModeParam === "discover"
    ? "discover"
    : queryModeParam === "theta"
    ? "theta"
    : queryModeParam === "strength"
      ? "strength"
      : queryModeParam === "leap"
        ? "leap"
        : queryModeParam === "garch"
          ? "garch"
          : queryModeParam === "vol-cone"
            ? "vol-cone"
            : "flow";
  const queryMode = defaultMode ?? parsedQueryMode;
  const [mode, setModeState] = useState<ScannerMode>(queryMode);
  const { data, syncing, error, lastSync, syncNow } = useScanner(mode === "flow");
  const theta = useThetaHarvester(mode === "theta");
  const strength = useStrengthConfirmation(mode === "strength");
  const leap = useLeap(mode === "leap");
  const garch = useGarchConvergence(mode === "garch");
  const volCone = useVolCone(mode === "vol-cone");
  const [thetaScanning, setThetaScanning] = useState(false);
  const [thetaScanError, setThetaScanError] = useState<string | null>(null);
  const [strengthScanning, setStrengthScanning] = useState(false);
  const [strengthScanError, setStrengthScanError] = useState<string | null>(null);
  const [leapScanning, setLeapScanning] = useState(false);
  const [leapScanError, setLeapScanError] = useState<string | null>(null);
  const [garchScanning, setGarchScanning] = useState(false);
  const [garchScanError, setGarchScanError] = useState<string | null>(null);
  const signals = data?.top_signals ?? [];
  const { sorted, sort, toggle } = useSort(signals, scannerSigExtract);
  const { isMobile, hasMounted } = useViewport();
  const [sortKey, setSortKey] = useState<ScannerSortKey>("score");

  useEffect(() => {
    setModeState(queryMode);
  }, [queryMode]);

  const setMode = (next: ScannerMode) => {
    setModeState(next);
    const params = new URLSearchParams(searchParams.toString());
    if (next === "flow") {
      params.delete("mode");
    } else {
      params.set("mode", next);
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const runThetaScan = async (ticker?: string, params?: ThetaScanParams) => {
    if (thetaScanning) return;
    const normalizedTicker = ticker?.trim().toUpperCase();
    setThetaScanError(null);
    setThetaScanning(true);
    try {
      const body: Record<string, unknown> = normalizedTicker
        ? { ticker: normalizedTicker }
        : { preset: "ndx100" };
      if (params) {
        body.min_dte = params.minDte;
        body.max_dte = params.maxDte;
        body.min_credit = params.minCredit;
      }
      const res = await fetch("/api/scanner/theta/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || `Theta scan failed (${res.status})`);
      }
      theta.syncNow();
    } catch (err) {
      setThetaScanError(err instanceof Error ? err.message : "Theta scan failed");
    } finally {
      setThetaScanning(false);
    }
  };

  const runStrengthScan = async (ticker?: string) => {
    if (strengthScanning) return;
    const normalizedTicker = ticker?.trim().toUpperCase();
    setStrengthScanError(null);
    setStrengthScanning(true);
    try {
      const res = await fetch("/api/scanner/strength/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(normalizedTicker ? { ticker: normalizedTicker } : { preset: "ndx100" }),
        cache: "no-store",
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || `Strength scan failed (${res.status})`);
      }
      strength.syncNow();
    } catch (err) {
      setStrengthScanError(err instanceof Error ? err.message : "Strength scan failed");
    } finally {
      setStrengthScanning(false);
    }
  };

  const runLeapScan = async (tickers?: string[]) => {
    if (leapScanning) return;
    setLeapScanError(null);
    setLeapScanning(true);
    try {
      const res = await fetch("/api/leap/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(tickers && tickers.length > 0 ? { tickers } : { preset: "largecaps" }),
        cache: "no-store",
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || `LEAP scan failed (${res.status})`);
      }
      leap.syncNow();
    } catch (err) {
      setLeapScanError(err instanceof Error ? err.message : "LEAP scan failed");
    } finally {
      setLeapScanning(false);
    }
  };

  const runGarchScan = async (tickers?: string[]) => {
    if (garchScanning) return;
    setGarchScanError(null);
    setGarchScanning(true);
    try {
      const res = await fetch("/api/garch-convergence/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(tickers && tickers.length > 0 ? { tickers } : { preset: "largecaps" }),
        cache: "no-store",
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(text || `GARCH scan failed (${res.status})`);
      }
      garch.syncNow();
    } catch (err) {
      setGarchScanError(err instanceof Error ? err.message : "GARCH scan failed");
    } finally {
      setGarchScanning(false);
    }
  };

  const modeTabs = (
    <ScannerModeTabs
      mode={mode}
      onModeChange={setMode}
      counts={{
        flow: data ? data.signals_found ?? 0 : undefined,
        theta: theta.data ? theta.data.theta_harvest_count ?? 0 : undefined,
        strength: strength.data ? strength.data.confirmed_strength_count ?? 0 : undefined,
        leap: leap.data ? (leap.data.results ?? []).filter((r) => r.is_mispriced).length : undefined,
        garch: garch.data ? (garch.data.pairs ?? []).filter((p) => p.gates_passed).length : undefined,
        "vol-cone": volCone.data && !volCone.data.missing ? volCone.data.hit_count : undefined,
      }}
    />
  );

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

  const mobileSortKeys: { key: ScannerSortKey; label: string }[] = [
    { key: "score", label: "Score" },
    { key: "strength", label: "Strength" },
    { key: "buy_ratio", label: "Buy %" },
    { key: "num_prints", label: "Prints" },
  ];

  if (mode === "theta") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <ThetaHarvesterScanner
          data={theta.data ?? null}
          loading={theta.loading}
          scanning={thetaScanning}
          error={thetaScanError || theta.error}
          lastSync={theta.lastSync}
          onScan={(params) => { void runThetaScan(undefined, params); }}
          onTickerScan={(ticker) => { void runThetaScan(ticker); }}
        />
      </div>
    );
  }

  if (mode === "strength") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <StrengthConfirmationScanner
          data={strength.data ?? null}
          loading={strength.loading}
          scanning={strengthScanning}
          error={strengthScanError || strength.error}
          lastSync={strength.lastSync}
          onScan={() => { void runStrengthScan(); }}
          onTickerScan={(ticker) => { void runStrengthScan(ticker); }}
        />
      </div>
    );
  }

  if (mode === "leap") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <LeapScanner
          data={leap.data ?? null}
          loading={leap.loading}
          scanning={leapScanning}
          error={leapScanError || leap.error}
          lastSync={leap.lastSync}
          onScan={() => { void runLeapScan(); }}
          onTickerScan={(tickers) => { void runLeapScan(tickers); }}
        />
      </div>
    );
  }

  if (mode === "garch") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <GarchConvergenceScanner
          data={garch.data ?? null}
          loading={garch.loading}
          scanning={garchScanning}
          error={garchScanError || garch.error}
          lastSync={garch.lastSync}
          onScan={() => { void runGarchScan(); }}
          onTickerScan={(tickers) => { void runGarchScan(tickers); }}
        />
      </div>
    );
  }

  if (mode === "vol-cone") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <VolConePanel />
      </div>
    );
  }

  if (mode === "discover") {
    return (
      <div className="scanner-page-shell">
        {modeTabs}
        <DiscoverSections />
      </div>
    );
  }

  if (hasMounted && isMobile) {
    const mobileSorted = [...signals].sort((a, b) => {
      const av = scannerSigExtract(a, sortKey) ?? 0;
      const bv = scannerSigExtract(b, sortKey) ?? 0;
      return typeof av === "number" && typeof bv === "number" ? bv - av : String(av).localeCompare(String(bv));
    });

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {modeTabs}
        {/* Mobile section header strip */}
        <div className="m-scanner-header">
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Sparkles size={13} style={{ color: "var(--text-muted)" }} />
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", color: "var(--text-primary)", textTransform: "uppercase" }}>
              Scanner
            </span>
            <InfoTooltip
              text={SECTION_TOOLTIPS["Scanner Signals"]}
              ariaLabel="Scanner Signals details"
              triggerTestId="scanner-mobile-title-tooltip"
              contentTestId="scanner-mobile-title-tooltip-content"
            />
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                fontWeight: 600,
                padding: "1px 6px",
                borderRadius: 4,
                background: "color-mix(in srgb, var(--positive) 14%, transparent)",
                color: "var(--positive)",
                border: "1px solid color-mix(in srgb, var(--positive) 28%, transparent)",
              }}
            >
              {data?.signals_found ?? 0}
            </span>
            {lastSync && (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>
                {new Date(lastSync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
          </div>
          <button
            type="button"
            className="tap-target"
            onClick={syncNow}
            disabled={syncing}
            aria-label="Rescan"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
              color: syncing ? "var(--text-muted)" : "var(--signal-core)",
              background: "none",
              border: "none",
              cursor: syncing ? "default" : "pointer",
              padding: "0 4px",
            }}
          >
            <Loader2 size={12} style={{ opacity: syncing ? 1 : 0.6, animation: syncing ? "spin 1s linear infinite" : "none" }} />
            {syncing ? "SCANNING" : "RESCAN"}
          </button>
        </div>

        {error && (
          <div style={{ padding: "8px 16px" }}>
            <div className="alert-item bearish">{error}</div>
          </div>
        )}

        {/* Sort bar */}
        {signals.length > 0 && (
          <div className="m-sortbar">
            {mobileSortKeys.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                className={`m-chip${sortKey === key ? " m-chip--active" : ""}`}
                onClick={() => setSortKey(key)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {signals.length === 0 && !syncing && !error && (
          <div style={{ padding: "24px 16px" }}>
            <SectionEmptyState icon={Sparkles} headline="No scanner signals" secondary="Waiting for initial scan..." />
          </div>
        )}

        {mobileSorted.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "8px 16px 16px" }} data-testid="mobile-scanner-list">
            {mobileSorted.map((row) => {
              const dirTone = scannerDirTone(row.direction);
              const dirLabel = row.direction === "ACCUMULATION" ? "ACCUM" : row.direction === "DISTRIBUTION" ? "DISTRIB" : row.direction;
              const sustainedSuffix = row.sustained_days > 0 ? ` ${row.sustained_days}d` : "";
              return (
                <SignalCard
                  key={`scanner-mobile-${row.ticker}`}
                  ticker={row.ticker}
                  score={Math.round(row.score)}
                  signals={[
                    {
                      label: `${dirLabel}${sustainedSuffix}`,
                      tone: dirTone,
                    },
                    {
                      label: row.signal,
                      tone: scannerSignalTone(row.signal),
                    },
                  ]}
                  stats={[
                    {
                      label: "Buy Ratio",
                      value: row.buy_ratio != null ? `${(row.buy_ratio * 100).toFixed(1)}%` : "---",
                    },
                    {
                      label: "Strength",
                      value: row.strength.toFixed(1),
                    },
                    {
                      label: "Prints",
                      value: row.num_prints.toLocaleString(),
                    },
                  ]}
                />
              );
            })}
          </div>
        )}

        {/* Sticky scan CTA */}
        <div className="m-sticky-cta">
          <button
            type="button"
            onClick={syncNow}
            disabled={syncing}
            style={{
              width: "100%",
              minHeight: 44,
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              background: syncing
                ? "color-mix(in srgb, var(--text-muted) 12%, transparent)"
                : "color-mix(in srgb, var(--signal-core) 14%, transparent)",
              color: syncing ? "var(--text-muted)" : "var(--signal-core)",
              border: `1px solid ${syncing ? "transparent" : "color-mix(in srgb, var(--signal-core) 30%, transparent)"}`,
              borderRadius: 4,
              cursor: syncing ? "default" : "pointer",
            }}
          >
            {syncing ? "Scanning..." : "Run Scan"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      {modeTabs}
      <div className="section">
        <div className="section-header">
          <h2 className="section-title">
            <Sparkles size={14} />
            Scanner Signals
            <InfoTooltip text={SECTION_TOOLTIPS["Scanner Signals"]} />
          </h2>
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
          <div className="section-body">
            <SectionEmptyState
              icon={Sparkles}
              headline="No scanner signals"
              secondary="Waiting for initial scan..."
            />
          </div>
        )}
        {signals.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<ScannerSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Signal", "signal")} />
                  <SortTh<ScannerSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Direction", "direction")} />
                  <SortTh<ScannerSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Score", "score")} />
                  <SortTh<ScannerSortKey> label="Strength" sortKey="strength" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Strength", "strength")} />
                  <SortTh<ScannerSortKey> label="Buy Ratio" sortKey="buy_ratio" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Buy Ratio", "buy-ratio")} />
                  <SortTh<ScannerSortKey> label="Sustained" sortKey="sustained_days" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Sustained", "sustained")} />
                  <SortTh<ScannerSortKey> label="Prints" sortKey="num_prints" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...scannerHelpProps("Prints", "prints")} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => {
                  const orderHref = flowOrderHref(row);
                  return (
                    <tr key={`scanner-${row.ticker}`}>
                      <td>
                        {orderHref ? (
                          <Link
                            href={orderHref}
                            className="ticker-link"
                            data-testid={`flow-order-link-${row.ticker}`}
                            title={`Open the ${row.ticker.toUpperCase()} options chain`}
                          >
                            {row.ticker}
                          </Link>
                        ) : (
                          <TickerLink ticker={row.ticker} />
                        )}
                      </td>
                      <td><span className={signalClass(row.signal)}>{row.signal}</span></td>
                      <td><span className={`pill ${dirClass(row.direction)}`}>{row.direction}</span></td>
                      <td className="right">
                        {row.score.toFixed(1)}
                        <SigMeter value={row.score} tone={row.direction === "ACCUMULATION" ? "pos" : row.direction === "DISTRIBUTION" ? "neg" : "mut"} />
                      </td>
                      <td className="right">{row.strength.toFixed(1)}</td>
                      <td className="right">{row.buy_ratio != null ? `${(row.buy_ratio * 100).toFixed(1)}%` : "—"}</td>
                      <td className="right">{row.sustained_days > 0 ? `${row.sustained_days}d` : "—"}</td>
                      <td className="right">{row.num_prints.toLocaleString()}</td>
                    </tr>
                  );
                })}
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

function discoverDpTone(dir: string): "pos" | "neg" | "warn" | "mut" {
  if (dir === "ACCUMULATION") return "pos";
  if (dir === "DISTRIBUTION") return "neg";
  return "mut";
}

function discoverBiasTone(bias: string): "pos" | "neg" | "warn" | "mut" {
  if (bias === "BULLISH" || bias === "CALLS") return "pos";
  if (bias === "BEARISH" || bias === "PUTS") return "neg";
  return "mut";
}

/**
 * Deep-link a discover candidate into its chain deck. Mirrors `leapOrderHref`
 * minus the contract params: discover rows are ticker-level (calls / puts are
 * alert counts, not strikes), so there is no expiry, strike, or right to seed
 * the order builder with. Null when the row cannot name a ticker.
 */
export function discoverOrderHref(candidate: DiscoverCandidate): string | null {
  const ticker = candidate.ticker.trim().toUpperCase();
  if (!ticker) return null;
  const params = new URLSearchParams({ deck: "c", src: "discover" });
  return `/${encodeURIComponent(ticker)}?${params.toString()}`;
}

function DiscoverTickerCell({ candidate }: { candidate: DiscoverCandidate }) {
  const href = discoverOrderHref(candidate);
  if (!href) return <TickerLink ticker={candidate.ticker} />;
  const ticker = candidate.ticker.trim().toUpperCase();
  return (
    <Link
      href={href}
      className="ticker-link"
      data-testid={`discover-order-link-${ticker}`}
      title={`Open the ${ticker} options chain`}
    >
      {candidate.ticker}
    </Link>
  );
}

export const DISCOVER_MOBILE_SORT_KEYS: { key: DiscoverSortKey; label: string }[] = [
  { key: "score", label: "Score" },
  { key: "dp_buy_ratio", label: "Buy %" },
  { key: "total_premium", label: "Premium" },
  { key: "sweeps", label: "Sweeps" },
  { key: "alerts", label: "Alerts" },
  { key: "ticker", label: "Ticker" },
  { key: "dp_strength", label: "Strength" },
  { key: "dp_direction", label: "DP Dir" },
  { key: "options_bias", label: "Bias" },
  { key: "sector", label: "Sector" },
];

function DiscoverSections() {
  const { data, syncing, error, lastSync } = useDiscover(true);
  const candidates = data?.candidates ?? [];
  const { sorted, sort, toggle } = useSort<DiscoverCandidate, DiscoverSortKey>(candidates, discoverExtract, "score", "desc");
  const { isMobile, hasMounted } = useViewport();
  const [discoverSortKey, setDiscoverSortKey] = useState<DiscoverSortKey>("score");

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

  const discoverMobileSortKeys = DISCOVER_MOBILE_SORT_KEYS;

  if (hasMounted && isMobile) {
    const mobileSortedCandidates = [...candidates].sort((a, b) => {
      const av = discoverExtract(a, discoverSortKey) ?? 0;
      const bv = discoverExtract(b, discoverSortKey) ?? 0;
      return typeof av === "number" && typeof bv === "number" ? bv - av : String(av).localeCompare(String(bv));
    });

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {error && (
          <div style={{ padding: "8px 16px" }}>
            <div className="alert-item bearish">{error}</div>
          </div>
        )}

        {candidates.length === 0 && !syncing && !error && (
          <div style={{ padding: "24px 16px" }}>
            <SectionEmptyState icon={Search} headline="No candidates found" secondary="Waiting for initial scan..." />
          </div>
        )}

        {/* Sort bar */}
        {candidates.length > 0 && (
          <div className="m-sortbar">
            {discoverMobileSortKeys.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                className={`m-chip${discoverSortKey === key ? " m-chip--active" : ""}`}
                onClick={() => setDiscoverSortKey(key)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {mobileSortedCandidates.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "8px 16px 16px" }} data-testid="mobile-discover-list">
            {mobileSortedCandidates.map((c) => {
              const orderHref = discoverOrderHref(c);
              const card = (
                <SignalCard
                  key={`discover-mobile-${c.ticker}`}
                  ticker={c.ticker}
                  score={Math.round(c.score)}
                  signals={[
                    {
                      label: c.dp_direction === "ACCUMULATION" ? "ACCUM" : c.dp_direction === "DISTRIBUTION" ? "DISTRIB" : c.dp_direction,
                      tone: discoverDpTone(c.dp_direction),
                    },
                    {
                      label: c.options_bias,
                      tone: discoverBiasTone(c.options_bias),
                    },
                  ]}
                  stats={[
                    {
                      label: "Buy Ratio",
                      value: `${(c.dp_buy_ratio * 100).toFixed(1)}%`,
                    },
                    {
                      label: "Premium",
                      value: fmtPremium(c.total_premium),
                    },
                    {
                      label: "Sweeps",
                      value: String(c.sweeps),
                    },
                    {
                      label: "Alerts",
                      value: String(c.alerts),
                    },
                  ]}
                />
              );
              if (!orderHref) return card;
              return (
                <Link
                  key={`discover-mobile-${c.ticker}`}
                  href={orderHref}
                  className="m-signal-card-link"
                  data-testid={`discover-order-link-${c.ticker.trim().toUpperCase()}`}
                >
                  {card}
                </Link>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="section">
        <div className="section-header">
          <h2 className="section-title">
            <Search size={14} />
            Discovery Candidates
            <InfoTooltip text={SECTION_TOOLTIPS["Discovery Candidates"]} />
          </h2>
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
          <div className="section-body">
            <SectionEmptyState
              icon={Search}
              headline="No candidates found"
              secondary="Waiting for initial scan..."
            />
          </div>
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
                    <td><DiscoverTickerCell candidate={c} /></td>
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

type JournalSortKey = "id" | "date" | "ticker" | "structure" | "decision" | "qty" | "entry_cost" | "max_risk" | "realized_pnl" | "ror" | "gates" | "edge";

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
    case "gates": return t.gates_passed?.join(", ") || t.gates_failed?.join(", ") || null;
    case "edge": return t.edge_analysis?.edge_type ?? null;
    default: return null;
  }
};

const JOURNAL_RANGE_PRESETS: { id: JournalRangePreset; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "7d", label: "7D" },
  { id: "mtd", label: "MTD" },
  { id: "ytd", label: "YTD" },
  { id: "all", label: "All" },
  { id: "custom", label: "Custom" },
];

function JournalSections() {
  const { data, loading, error, syncWithIB, syncing, lastSyncResult } = useJournal();
  const [syncError, setSyncError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [rangePreset, setRangePreset] = useState<JournalRangePreset>("mtd");
  const mtdBounds = useMemo(() => rangeForPreset("mtd"), []);
  const [customFrom, setCustomFrom] = useState(mtdBounds.from ?? "");
  const [customTo, setCustomTo] = useState(mtdBounds.to ?? "");
  const { isMobile, hasMounted } = useViewport();
  const showMobileJournal = isMobile && hasMounted;

  const trades = useMemo(() => {
    if (!data?.trades) return [];
    return [...data.trades].sort((a, b) => b.id - a.id);
  }, [data]);

  const { from: rangeFrom, to: rangeTo } = useMemo(() => {
    if (rangePreset === "custom") {
      return {
        from: customFrom || null,
        to: customTo || null,
      };
    }
    return rangeForPreset(rangePreset);
  }, [rangePreset, customFrom, customTo]);

  // List: activity in range (opens + closes). All time: full journal.
  const rangedTrades = useMemo(() => {
    if (rangeFrom == null && rangeTo == null) return trades;
    return filterTradesByRange(trades, rangeFrom, rangeTo, "activity");
  }, [trades, rangeFrom, rangeTo]);

  // Summary: realized P&L for closes whose close date is in range.
  // Open count / trade count reflect activity listed in the table.
  const rangeSummary = useMemo(() => {
    const closedInRange =
      rangeFrom == null && rangeTo == null
        ? trades.filter((t) => isClosedTrade(t))
        : filterTradesByRange(trades, rangeFrom, rangeTo, "closed");
    const base = summarizeRangePnl(closedInRange);
    return {
      ...base,
      openCount: rangedTrades.filter((t) => !isClosedTrade(t)).length,
      tradeCount: rangedTrades.length,
    };
  }, [trades, rangedTrades, rangeFrom, rangeTo]);

  const extractSearchText = useCallback(
    (t: TradeEntry) => `${t.ticker} ${t.structure} ${t.decision} ${t.date} ${t.edge_analysis?.edge_type ?? ""}`,
    [],
  );
  const { filtered, query, setQuery } = useTableFilter(rangedTrades, extractSearchText);
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

  const applyPreset = useCallback((preset: JournalRangePreset) => {
    setRangePreset(preset);
    if (preset !== "custom") {
      const bounds = rangeForPreset(preset);
      if (bounds.from) setCustomFrom(bounds.from);
      if (bounds.to) setCustomTo(bounds.to);
    }
  }, []);

  const fmtJournalUsd = (v: number | undefined | null) => {
    if (v == null) return "--";
    const abs = Math.abs(v);
    const formatted = abs >= 1000 ? `$${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}` : `$${abs.toFixed(2)}`;
    return v < 0 ? `-${formatted}` : formatted;
  };

  const fmtSignedJournalUsd = (v: number) => {
    if (v === 0) return fmtJournalUsd(0);
    const sign = v > 0 ? "+" : "-";
    return `${sign}${fmtJournalUsd(Math.abs(v))}`;
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

  const rangeLabel =
    rangeFrom && rangeTo
      ? rangeFrom === rangeTo
        ? rangeFrom
        : `${rangeFrom} to ${rangeTo}`
      : "All time";

  return (
    <>
      <div className="section" data-testid="journal-section">
        <div className="section-header">
          <div className="section-title">
            <Wrench size={14} />
            Trade Journal
            <InfoTooltip text={SECTION_TOOLTIPS["Trade Journal"]} />
          </div>
          <div className="section-header-actions">
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
                totalCount={rangedTrades.length}
              />
            ) : null}
            <span className="pill defined" data-testid="journal-trade-count">
              {rangedTrades.length} TRADES
            </span>
          </div>
        </div>

        {trades.length > 0 && (
          <div className="journal-range-bar" data-testid="journal-range-bar">
            <div className="journal-range-presets" role="toolbar" aria-label="Journal date range">
              {JOURNAL_RANGE_PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`journal-range-chip${rangePreset === p.id ? " journal-range-chip--active" : ""}`}
                  aria-pressed={rangePreset === p.id}
                  onClick={() => applyPreset(p.id)}
                  data-testid={`journal-range-${p.id}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {rangePreset === "custom" && (
              <div className="journal-range-custom">
                <label className="journal-range-custom__field">
                  <span>From</span>
                  <input
                    type="date"
                    value={customFrom}
                    onChange={(e) => setCustomFrom(e.target.value)}
                    data-testid="journal-range-from"
                  />
                </label>
                <label className="journal-range-custom__field">
                  <span>To</span>
                  <input
                    type="date"
                    value={customTo}
                    onChange={(e) => setCustomTo(e.target.value)}
                    data-testid="journal-range-to"
                  />
                </label>
              </div>
            )}
            <div
              className="journal-pnl-strip"
              data-testid="journal-pnl-strip"
              title={`Realized P&L for closes in ${rangeLabel} (America/New_York). Opens in range are listed but not summed.`}
            >
              <div className="journal-pnl-strip__stat">
                <span className="journal-pnl-strip__label">Realized P&L</span>
                <span
                  className={`journal-pnl-strip__value mono ${rangeSummary.realizedPnl >= 0 ? "positive" : "negative"}`}
                  data-testid="journal-range-realized"
                >
                  {fmtSignedJournalUsd(rangeSummary.realizedPnl)}
                </span>
              </div>
              <div className="journal-pnl-strip__stat">
                <span className="journal-pnl-strip__label">Closed</span>
                <span className="journal-pnl-strip__value mono" data-testid="journal-range-closed">
                  {rangeSummary.closedCount}
                </span>
              </div>
              <div className="journal-pnl-strip__stat">
                <span className="journal-pnl-strip__label">W / L</span>
                <span className="journal-pnl-strip__value mono" data-testid="journal-range-wl">
                  {rangeSummary.winners} / {rangeSummary.losers}
                </span>
              </div>
              <div className="journal-pnl-strip__stat">
                <span className="journal-pnl-strip__label">Open in range</span>
                <span className="journal-pnl-strip__value mono" data-testid="journal-range-open">
                  {rangeSummary.openCount}
                </span>
              </div>
              <div className="journal-pnl-strip__stat journal-pnl-strip__stat--range">
                <span className="journal-pnl-strip__label">Range</span>
                <span className="journal-pnl-strip__value mono" data-testid="journal-range-label">
                  {rangeLabel}
                </span>
              </div>
            </div>
          </div>
        )}

        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {syncError && <div className="section-body"><div className="alert-item bearish">IB Sync: {syncError}</div></div>}
        {loading && <div className="section-body p-6"><SpectralLoader label="Loading journal" /></div>}
        {!loading && trades.length === 0 && !error && (
          <div className="section-body">
            <SectionEmptyState icon={Wrench} headline="No trades in journal" />
          </div>
        )}
        {!loading && trades.length > 0 && rangedTrades.length === 0 && (
          <div className="section-body">
            <SectionEmptyState
              icon={Wrench}
              headline="No trades in this range"
              secondary="No journal activity on the selected America/New_York dates. Widen the range or switch to All."
              action={{ label: "Show all", onClick: () => applyPreset("all") }}
              testId="journal-range-empty"
            />
          </div>
        )}
        {rangedTrades.length > 0 && showMobileJournal && (
          <div className="section-body">
            <MobileJournalList trades={sortedTrades} />
          </div>
        )}
        {rangedTrades.length > 0 && !showMobileJournal && (
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
                  <SortTh<JournalSortKey> label="Gates" sortKey="gates" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<JournalSortKey> label="Edge" sortKey="edge" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
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
                        <td className="right">{qty ?? "--"}</td>
                        <td className="right">{fmtJournalUsd(cost)}</td>
                        <td className="right">{fmtJournalUsd(t.max_risk)}</td>
                        <td className="right"><span className={pnlClass(t.realized_pnl)}>{fmtJournalUsd(t.realized_pnl)}{t.return_on_risk != null ? ` (${(t.return_on_risk * 100) >= 0 ? "+" : ""}${(t.return_on_risk * 100).toFixed(1)}%)` : ""}</span></td>
                        <td className="right">{t.return_on_risk != null ? `${(t.return_on_risk * 100).toFixed(1)}%` : "--"}</td>
                        <td className="cell-muted">{t.gates_passed?.join(", ") || t.gates_failed?.join(", ") || "--"}</td>
                        <td className="cell-muted">{t.edge_analysis?.edge_type ?? "--"}</td>
                      </tr>
                      {hasLegs && isExpanded && t.legs!.map((leg, i) => (
                        <tr key={`${t.id}-leg-${i}`} className="leg-row">
                          <td />
                          <td className="cell-muted">{leg.expiry ?? "--"}</td>
                          <td />
                          <td className="cell-muted">{leg.type ?? "--"}{leg.strike != null ? ` $${leg.strike}` : ""}</td>
                          <td />
                          <td className="right cell-muted">{leg.contracts ?? "--"}</td>
                          <td className="right cell-muted">{leg.open_price != null ? `$${leg.open_price.toFixed(2)}` : "--"}</td>
                          <td className="right cell-muted">{leg.close_price != null ? `$${leg.close_price.toFixed(2)}` : "--"}</td>
                          <td className="right"><span className={pnlClass(leg.leg_pnl)}>{leg.leg_pnl != null ? fmtJournalUsd(leg.leg_pnl) : "--"}</span></td>
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

type OpenOrderKey =
  | "symbol"
  | "action"
  | "orderType"
  | "totalQuantity"
  | "limitPrice"
  | "lastPrice"
  | "deltaFill"
  | "implied"
  | "implied_mv"
  | "status"
  | "tif"
  | "actions";

type OrderToggleableKey =
  | "orderType"
  | "totalQuantity"
  | "limitPrice"
  | "lastPrice"
  | "deltaFill"
  | "implied"
  | "implied_mv"
  | "tif";

const ORDER_COLUMNS: readonly ColumnsToggleEntry<OrderToggleableKey>[] = [
  { key: "orderType", label: "Type" },
  { key: "totalQuantity", label: "Quantity" },
  { key: "limitPrice", label: "Limit Price" },
  { key: "lastPrice", label: "Last Price" },
  { key: "deltaFill", label: "Δ Fill" },
  { key: "implied", label: "Implied" },
  { key: "implied_mv", label: "Implied MV" },
  { key: "tif", label: "TIF" },
];

const ORDER_COLUMN_DEFAULTS: Record<OrderToggleableKey, boolean> = {
  orderType: true,
  totalQuantity: true,
  limitPrice: true,
  lastPrice: true,
  deltaFill: true,
  implied: false,
  implied_mv: false,
  tif: true,
};

const MODIFY_DISABLED_TITLE = "Only LMT and STP LMT orders can be modified";

type OrderColumnVisibility = Record<OrderToggleableKey, boolean>;

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
  riskFreeRate = 0,
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
      case "deltaFill": {
        if (item.kind === "combo") {
          const last = resolveOpenOrderComboPrice(item.orders, prices);
          const action = item.orders[0]?.action ?? "BUY";
          return distanceToFill({
            action,
            limitPrice: item.limitPrice,
            lastPrice: last,
          })?.delta ?? null;
        }
        return distanceToFill({
          action: item.order.action,
          limitPrice: item.order.limitPrice,
          lastPrice: resolveOrderLastPrice(item.order, prices, portfolio),
        })?.delta ?? null;
      }
      case "implied":
        if (!prices) return null;
        return item.kind === "combo"
          ? computeOrderImpliedValue(item.orders, prices, { riskFreeRate }).netPerContract
          : resolveOrderImpliedValue(item.order, prices, riskFreeRate);
      case "implied_mv":
        if (!prices) return null;
        return item.kind === "combo"
          ? resolveComboImpliedMv(item.orders, prices, riskFreeRate)
          : resolveSingleOrderImpliedMv(item.order, prices, riskFreeRate);
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

/** Black-Scholes implied per-share value of an order's contract. Single OPT only;
 *  STK and BAG return null (BAG aggregation is handled at the combo row level
 *  via `computeOrderImpliedValue`). */
function resolveOrderImpliedValue(
  order: OpenOrder,
  prices: Record<string, PriceData>,
  riskFreeRate = 0,
): number | null {
  const c = order.contract;
  if (c.secType !== "OPT") return null;
  if (c.strike == null || !c.right || !c.expiry) return null;
  const type: "Call" | "Put" | null =
    c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : null;
  if (!type) return null;
  return computeLegImpliedValue(
    {
      ticker: c.symbol,
      expiry: c.expiry,
      strike: c.strike,
      type,
      direction: order.action === "BUY" ? "LONG" : "SHORT",
      contracts: Math.abs(order.totalQuantity),
    },
    prices,
    { riskFreeRate },
  ).perContract;
}

function OrderImpliedCell({ price }: { price: number | null }) {
  return (
    <td className="right cell-muted" title="Black-Scholes implied value at current spot">
      {price != null ? fmtPrice(price) : "—"}
    </td>
  );
}

/** Implied dollar notional for a combo order: net per-share × baseQuantity × 100,
 *  using the same baseQuantity convention as resolveOpenOrderComboPrice (Math.min
 *  across leg sizes). Sign reflects net debit (positive) vs credit (negative). */
function resolveComboImpliedMv(
  orders: OpenOrder[],
  prices: Record<string, PriceData>,
  riskFreeRate = 0,
): number | null {
  const r = computeOrderImpliedValue(orders, prices, { riskFreeRate });
  if (r.netPerContract == null) return null;
  const sizes = orders.map((o) => Math.abs(o.totalQuantity)).filter((q) => q > 0);
  if (sizes.length === 0) return null;
  const base = Math.min(...sizes);
  return r.netPerContract * base * 100;
}

/** Implied dollar notional for a single OPT order: per-share × |qty| × 100,
 *  signed by BUY/SELL action. STK/BAG/null on missing inputs. */
function resolveSingleOrderImpliedMv(
  order: OpenOrder,
  prices: Record<string, PriceData>,
  riskFreeRate = 0,
): number | null {
  const perShare = resolveOrderImpliedValue(order, prices, riskFreeRate);
  if (perShare == null) return null;
  const sign = order.action === "BUY" ? 1 : -1;
  return sign * perShare * Math.abs(order.totalQuantity) * 100;
}

function OrderImpliedMvCell({ value }: { value: number | null }) {
  return (
    <td
      className={`right ${value != null ? (value >= 0 ? "positive" : "negative") : "cell-muted"}`}
      title="Implied market value: BS price × contracts × 100, signed"
    >
      {value != null
        ? `${value >= 0 ? "+" : "-"}${fmtUsd(Math.abs(value))}`
        : "—"}
    </td>
  );
}

type ExecOrderKey = "symbol" | "side" | "quantity" | "avgPrice" | "commission" | "realizedPNL" | "time";
type ExecGroupKey = "position" | "action" | "quantity" | "netPrice" | "commission" | "pnl" | "time";

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
  const { isMobile, hasMounted } = useViewport();
  const showMobileOrders = isMobile && hasMounted;
  const riskFreeRate = useRiskFreeRate();
  const openOrderExtract = useMemo(() => makeOpenOrderExtract(prices, portfolio, riskFreeRate), [prices, portfolio, riskFreeRate]);
  // Implied columns only meaningful when at least one open order is an option.
  const showImplied = useMemo(
    () =>
      (orders?.open_orders ?? []).some(
        (o) => o.contract.secType === "OPT" || o.contract.secType === "BAG",
      ),
    [orders],
  );
  const { visible: orderColumns, toggle: toggleOrderColumn, reset: resetOrderColumns } =
    useColumnVisibility<OrderToggleableKey>("orders-open", ORDER_COLUMN_DEFAULTS);
  const visibleOrderColumnEntries = useMemo<readonly ColumnsToggleEntry<OrderToggleableKey>[]>(
    () =>
      showImplied
        ? ORDER_COLUMNS
        : ORDER_COLUMNS.filter((c) => c.key !== "implied" && c.key !== "implied_mv"),
    [showImplied],
  );
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
  const [cancelCombo, setCancelCombo] = useState<OpenOrder[] | null>(null);
  const [modifyTarget, setModifyTarget] = useState<{
    modalOrder: OpenOrder;
    requestOrder: OpenOrder;
    cancelOrders?: ModifyOrderRequest["cancelOrders"];
  } | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [keyboardSelectedKey, setKeyboardSelectedKey] = useState<string | null>(null);
  const [bulkSelectedKeys, setBulkSelectedKeys] = useState<Set<string>>(() => new Set());
  const [openOrdersDensity, setOpenOrdersDensity] = useState<OpenOrdersDensity>(() => {
    if (typeof window === "undefined") return "comfortable";
    return parseOpenOrdersDensity(window.localStorage.getItem(OPEN_ORDERS_DENSITY_KEY));
  });
  const openOrdersFilterRef = useRef<HTMLInputElement>(null);

  const clearCancelDialog = useCallback(() => {
    setCancelTarget(null);
    setCancelCombo(null);
  }, []);

  const clearBulkSelection = useCallback(() => {
    setBulkSelectedKeys(new Set());
  }, []);

  const handleCancel = useCallback(async () => {
    const combo = cancelCombo;
    const single = cancelTarget;
    if (!combo?.length && !single) return;
    setActionLoading(true);
    try {
      if (combo && combo.length > 0) {
        for (const order of combo) {
          await requestCancel(order);
        }
      } else if (single) {
        await requestCancel(single);
      }
    } finally {
      setActionLoading(false);
      clearCancelDialog();
      clearBulkSelection();
    }
  }, [cancelCombo, cancelTarget, requestCancel, clearCancelDialog, clearBulkSelection]);

  const openModifyForRow = useCallback((row: OpenOrderDisplayRow) => {
    if (row.kind === "combo") {
      if (!row.orders.every((o) => o.orderType === "LMT" || o.orderType === "STP LMT")) return;
      const target = buildGroupedComboModifyTarget(row);
      setModifyTarget({
        modalOrder: target.modalOrder,
        requestOrder: row.orders[0],
        cancelOrders: target.cancelOrders,
      });
      return;
    }
    if (row.order.orderType !== "LMT" && row.order.orderType !== "STP LMT") return;
    setModifyTarget({ modalOrder: row.order, requestOrder: row.order });
  }, []);

  const openCancelForRow = useCallback((row: OpenOrderDisplayRow) => {
    if (row.kind === "combo") {
      setCancelTarget(null);
      setCancelCombo(row.orders);
      return;
    }
    setCancelCombo(null);
    setCancelTarget(row.order);
  }, []);

  const openBulkCancelSelected = useCallback(() => {
    const orders = flattenSelectedOpenOrders(openFilter.filtered, bulkSelectedKeys);
    if (orders.length === 0) return;
    setCancelTarget(null);
    setCancelCombo(orders);
  }, [openFilter.filtered, bulkSelectedKeys]);

  // Desktop open-orders shortcuts: / filter, M modify, X cancel.
  useEffect(() => {
    if (showMobileOrders) return;

    function onKeyDown(e: KeyboardEvent) {
      if (cancelTarget || cancelCombo || modifyTarget) return;

      const action = resolveOrdersShortcut({
        key: e.key,
        isTyping: isEditableKeyboardTarget(document.activeElement),
        hasModifier: e.metaKey || e.ctrlKey || e.altKey,
        selectedRowKey: keyboardSelectedKey,
        canModifySelected: (() => {
          if (!keyboardSelectedKey) return false;
          const row = openFilter.filtered.find((r) => openOrderRowKey(r) === keyboardSelectedKey);
          if (!row) return false;
          return canModifyDisplayRow(
            row,
            (o) => o.orderType === "LMT" || o.orderType === "STP LMT",
          );
        })(),
      });
      if (!action) return;

      e.preventDefault();
      if (action.type === "focus-filter") {
        openOrdersFilterRef.current?.focus();
        openOrdersFilterRef.current?.select();
        return;
      }

      const row = openFilter.filtered.find((r) => openOrderRowKey(r) === keyboardSelectedKey);
      if (!row) return;
      if (action.type === "modify") openModifyForRow(row);
      if (action.type === "cancel") openCancelForRow(row);
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [
    showMobileOrders,
    cancelTarget,
    cancelCombo,
    modifyTarget,
    keyboardSelectedKey,
    openFilter.filtered,
    openModifyForRow,
    openCancelForRow,
  ]);

  // Drop bulk selection keys that no longer appear in the filtered list.
  useEffect(() => {
    const visible = new Set(openFilter.filtered.map(openOrderRowKey));
    setBulkSelectedKeys((prev) => {
      let changed = false;
      const next = new Set<string>();
      for (const key of prev) {
        if (visible.has(key)) next.add(key);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [openFilter.filtered]);

  const toggleOpenOrdersDensity = useCallback(() => {
    setOpenOrdersDensity((prev) => {
      const next: OpenOrdersDensity = prev === "compact" ? "comfortable" : "compact";
      if (typeof window !== "undefined") {
        window.localStorage.setItem(OPEN_ORDERS_DENSITY_KEY, next);
      }
      return next;
    });
  }, []);

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
    // Belt-and-suspenders: API should already day-cut, but live sync payloads
    // can still carry a multi-day IB fills list under "Today's Executed".
    const todayFills = filterExecutedToEtToday(orders?.executed_orders ?? []);
    return [...cancelRows, ...todayFills];
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
  const execGroupExtract = useCallback((g: PositionFillGroup, key: ExecGroupKey): string | number | null => {
    switch (key) {
      case "position": return g.symbol;
      case "action": return g.fills[0]?.side === "CANCELLED" ? "CANCELLED" : g.isClosing ? "CLOSE" : "OPEN";
      case "quantity": return g.totalQuantity;
      case "netPrice": return g.netPrice;
      case "commission": return g.totalCommission;
      case "pnl": return g.totalPnL;
      case "time": return g.time;
      default: return null;
    }
  }, []);
  const execGroupSort = useSort(execFilter.filtered, execGroupExtract, "time", "desc");

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
      <div className="section" data-testid="orders-loading">
        <div className="section-header">
          <h2 className="section-title">
            <ClipboardList size={14} />
            Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </h2>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body p-6">
          <SpectralLoader label="Loading orders" />
        </div>
      </div>
    );
  }

  const canModify = (o: OpenOrder) => o.orderType === "LMT" || o.orderType === "STP LMT";
  const now = new Date();
  const openOrdersSummary = summarizeOpenOrderRows(openOrderRows, now);
  const sessionCounts = summarizeSessionWindows(openOrderRows, now);
  const lastSyncLabel = orders.last_sync
    ? formatRelativeTime(orders.last_sync)
    : null;

  return (
    <>
      <CancelOrderDialog
        order={cancelTarget}
        orders={cancelCombo}
        loading={actionLoading}
        onConfirm={handleCancel}
        onClose={clearCancelDialog}
      />
      <ModifyOrderModal
        order={modifyTarget?.modalOrder ?? null}
        loading={actionLoading}
        prices={prices}
        portfolio={portfolio}
        // R-112: the close-out branch must know what is already working.
        openOrders={orders}
        onConfirm={handleModify}
        onClose={() => setModifyTarget(null)}
      />

      <div className="orders-command-strip" data-testid="orders-command-strip">
        <span className="orders-command-strip__stat">
          <span className="orders-command-strip__label">Working</span>
          <span className="orders-command-strip__value">{openOrdersSummary.workingCount}</span>
        </span>
        <span className="orders-command-strip__stat">
          <span className="orders-command-strip__label">Partial</span>
          <span className="orders-command-strip__value">{openOrdersSummary.partialCount}</span>
        </span>
        <span className="orders-command-strip__stat">
          <span className="orders-command-strip__label">Fills today</span>
          <span className="orders-command-strip__value">{positionGroups.length}</span>
        </span>
        {lastSyncLabel && (
          <span className="orders-command-strip__stat" title={orders.last_sync ?? undefined}>
            <span className="orders-command-strip__label">Last sync</span>
            <span className="orders-command-strip__value">{lastSyncLabel}</span>
          </span>
        )}
        <span className="orders-command-strip__jumps">
          <a className="orders-command-strip__jump" href="#orders-open">Open</a>
          <a className="orders-command-strip__jump" href="#orders-executed">Executed</a>
          <a className="orders-command-strip__jump" href="#orders-historical">Historical</a>
          <a className="orders-command-strip__jump" href="#orders-cash">Cash</a>
        </span>
      </div>

      <div className="section" id="orders-open">
        <div className="section-header">
          <h2 className="section-title">
            <ClipboardList size={14} />
            Open Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </h2>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {!showMobileOrders && bulkSelectedKeys.size > 0 && (
              <button
                type="button"
                className="btn-order-action btn-cancel"
                data-testid="cancel-selected-orders"
                onClick={openBulkCancelSelected}
              >
                Cancel selected ({bulkSelectedKeys.size})
              </button>
            )}
            {!showMobileOrders && openOrderRows.length > 0 && (
              <button
                type="button"
                className="btn-secondary orders-density-toggle"
                data-testid="orders-density-toggle"
                title={openOrdersDensity === "compact" ? "Switch to comfortable density" : "Switch to compact density"}
                onClick={toggleOpenOrdersDensity}
              >
                {openOrdersDensity === "compact" ? "Comfortable" : "Compact"}
              </button>
            )}
            <ColumnsToggle<OrderToggleableKey>
              columns={visibleOrderColumnEntries}
              visible={orderColumns}
              onToggle={toggleOrderColumn}
              onReset={resetOrderColumns}
            />
            <TableSearch
              query={openFilter.query}
              setQuery={openFilter.setQuery}
              placeholder="Filter orders..."
              resultCount={openFilter.filtered.length}
              totalCount={openSort.sorted.length}
              inputId="orders-open-filter"
              inputRef={openOrdersFilterRef}
            />
            <span className="pill defined">{orders.open_count} ORDERS</span>
            <span className="pill order-session-count" data-testid="open-orders-rth-count">
              {sessionCounts.rth} RTH
            </span>
            <span className="pill order-session-count order-session-count--extended" data-testid="open-orders-ext-count">
              {sessionCounts.ext} EXT
            </span>
          </div>
        </div>
        <div className="section-body">
          {openOrderRows.length === 0 ? (
            <SectionEmptyState
              icon={Inbox}
              headline="No working orders"
              secondary="Place an order from any ticker view to populate this list."
              testId="open-orders-empty"
            />
          ) : showMobileOrders ? (
            <MobileOrderList
              rows={openFilter.filtered}
              portfolioPositions={portfolio?.positions}
              pendingCancelPermIds={pendingCancels}
              pendingModifyPermIds={pendingModifies}
              canModify={canModify}
              onRequestCancel={(single, combo) => {
                if (single) {
                  setCancelCombo(null);
                  setCancelTarget(single);
                } else if (combo) {
                  setCancelTarget(null);
                  setCancelCombo(combo);
                }
              }}
              onRequestModify={(single, combo) => {
                if (single) {
                  setModifyTarget({ modalOrder: single, requestOrder: single });
                } else if (combo && combo.length > 0) {
                  const comboRow = openFilter.filtered.find(
                    (row) => row.kind === "combo" && row.orders === combo,
                  );
                  if (comboRow && comboRow.kind === "combo") {
                    const target = buildGroupedComboModifyTarget(comboRow);
                    setModifyTarget({
                      modalOrder: target.modalOrder,
                      requestOrder: combo[0],
                      cancelOrders: target.cancelOrders,
                    });
                  }
                }
              }}
            />
          ) : (
            <div
              className={`table-wrap${openOrdersDensity === "compact" ? " table-wrap--compact" : ""}`}
              data-density={openOrdersDensity}
            >
            <table data-testid="open-orders-table">
              <thead>
                <tr>
                  <th className="open-order-select-th" scope="col">
                    <input
                      type="checkbox"
                      aria-label="Select all open orders"
                      data-testid="open-orders-select-all"
                      checked={
                        openFilter.filtered.length > 0
                        && openFilter.filtered.every((row) => bulkSelectedKeys.has(openOrderRowKey(row)))
                      }
                      onChange={(e) => {
                        setBulkSelectedKeys(
                          setAllSelectionKeys(
                            openFilter.filtered.map(openOrderRowKey),
                            e.target.checked,
                          ),
                        );
                      }}
                    />
                  </th>
                  <SortTh<OpenOrderKey> label="Symbol" sortKey="symbol" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Action" sortKey="action" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  {orderColumns.orderType && <SortTh<OpenOrderKey> label="Type" sortKey="orderType" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {orderColumns.totalQuantity && <SortTh<OpenOrderKey> label="Quantity" sortKey="totalQuantity" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {orderColumns.limitPrice && <SortTh<OpenOrderKey> label="Limit Price" sortKey="limitPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {orderColumns.lastPrice && <SortTh<OpenOrderKey> label="Last Price" sortKey="lastPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {orderColumns.deltaFill && <SortTh<OpenOrderKey> label="Δ Fill" sortKey="deltaFill" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {showImplied && orderColumns.implied && <SortTh<OpenOrderKey> label="Implied" sortKey="implied" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  {showImplied && orderColumns.implied_mv && <SortTh<OpenOrderKey> label="Implied MV" sortKey="implied_mv" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  <SortTh<OpenOrderKey> label="Status" sortKey="status" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  {orderColumns.tif && <SortTh<OpenOrderKey> label="TIF" sortKey="tif" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />}
                  <th className="actions-th">Actions</th>
                </tr>
              </thead>
              <tbody>
                {openFilter.filtered.map((o) => {
                  const rowKey = openOrderRowKey(o);
                  const isKeyboardSelected = keyboardSelectedKey === rowKey;
                  const isBulkSelected = bulkSelectedKeys.has(rowKey);
                  const session = classifyDisplayRowSession(o, now);
                  const sessionRowClass = session.eligibility === "extended" ? "open-order-row--ext" : "";
                  const selectCell = (
                    <td className="open-order-select-td" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select order ${rowKey}`}
                        data-testid={`open-order-select-${rowKey}`}
                        checked={isBulkSelected}
                        onChange={(e) => {
                          setBulkSelectedKeys((prev) =>
                            toggleSelectionKey(prev, rowKey, e.target.checked),
                          );
                        }}
                      />
                    </td>
                  );
                  if (o.kind === "combo") {
                    const comboCanModify = o.orders.every(canModify);
                    const comboModifyTarget = buildGroupedComboModifyTarget(o);
                    const isPendingCancel = o.orders.some((order) => pendingCancels.has(order.permId));
                    const isPendingModify = o.orders.some((order) => pendingModifies.has(order.permId));
                    const isPending = isPendingCancel || isPendingModify;
                    const partialLeg = o.orders.find((leg) => isPartialFill(leg));
                    const comboQtyLabel = partialLeg
                      ? formatFillQuantity(partialLeg)
                      : String(o.totalQuantity);
                    const comboLast = resolveOpenOrderComboPrice(o.orders, prices);
                    const comboDistance = distanceToFill({
                      action: o.orders[0]?.action ?? "BUY",
                      limitPrice: o.limitPrice,
                      lastPrice: comboLast,
                    });
                    const comboStatus = mapOrderStatus(o.status, {
                      filled: partialLeg?.filled,
                      remaining: partialLeg?.remaining,
                      isPendingCancel,
                      isPendingModify,
                    });
                    const rowClass = [
                      isPendingCancel ? "row-pending-cancel" : "",
                      isPendingModify ? "row-pending-modify" : "",
                      isKeyboardSelected ? "open-order-row--selected" : "",
                      sessionRowClass,
                      "open-order-row",
                    ].filter(Boolean).join(" ");

                    return (
                      <tr
                        key={o.id}
                        className={rowClass}
                        tabIndex={0}
                        data-row-key={rowKey}
                        data-testid={`open-order-row-${rowKey}`}
                        aria-selected={isKeyboardSelected}
                        onClick={() => setKeyboardSelectedKey(rowKey)}
                        onFocus={() => setKeyboardSelectedKey(rowKey)}
                      >
                        {selectCell}
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
                        {orderColumns.orderType && <td>{o.structure}</td>}
                        {orderColumns.totalQuantity && <td className="right">{comboQtyLabel}</td>}
                        {orderColumns.limitPrice && (
                          <td className="right">
                            <span className={isPendingModify ? "status-modifying" : ""}>
                              {isPendingModify ? "—" : o.limitPrice != null ? fmtPrice(o.limitPrice) : "—"}
                            </span>
                          </td>
                        )}
                        {orderColumns.lastPrice && (
                          <OrderPriceCell price={comboLast} />
                        )}
                        {orderColumns.deltaFill && (
                          <td className="right">
                            {comboDistance ? (
                              <span className={`order-delta order-delta--${comboDistance.urgency}`}>
                                {formatDistanceDelta(comboDistance.delta)}
                              </span>
                            ) : (
                              "--"
                            )}
                          </td>
                        )}
                        {showImplied && orderColumns.implied && (
                          <OrderImpliedCell
                            price={prices ? computeOrderImpliedValue(o.orders, prices, { riskFreeRate }).netPerContract : null}
                          />
                        )}
                        {showImplied && orderColumns.implied_mv && (
                          <OrderImpliedMvCell
                            value={prices ? resolveComboImpliedMv(o.orders, prices, riskFreeRate) : null}
                          />
                        )}
                        <td className="open-order-status-cell">
                          <span
                            className={statusPillClass(comboStatus.tone)}
                            title={comboStatus.raw}
                          >
                            {comboStatus.label}
                          </span>
                          <SessionWindowChip session={session} />
                        </td>
                        {orderColumns.tif && <td>{o.tif}</td>}
                        <td className="actions-cell">
                          {isPending ? (
                            <span className="cancel-pending-label">PENDING</span>
                          ) : (
                            <>
                              <button
                                className="btn-order-action btn-modify"
                                disabled={!comboCanModify}
                                title={comboCanModify ? "Modify combo order" : MODIFY_DISABLED_TITLE}
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
                                onClick={() => {
                                  setCancelTarget(null);
                                  setCancelCombo(o.orders);
                                }}
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
                  const singleLast = resolveOrderLastPrice(o.order, prices, portfolio);
                  const singleDistance = distanceToFill({
                    action: o.order.action,
                    limitPrice: o.order.limitPrice,
                    lastPrice: singleLast,
                  });
                  const singleStatus = mapOrderStatus(o.order.status, {
                    filled: o.order.filled,
                    remaining: o.order.remaining,
                    isPendingCancel,
                    isPendingModify,
                    extendedFillLive: isExtendedFillLive(session),
                  });
                  const intent = resolveOrderIntent(o.order, portfolio?.positions);
                  const singleRowClass = [
                    isPendingCancel ? "row-pending-cancel" : "",
                    isPendingModify ? "row-pending-modify" : "",
                    isKeyboardSelected ? "open-order-row--selected" : "",
                    sessionRowClass,
                    "open-order-row",
                  ].filter(Boolean).join(" ");
                  return (
                    <tr
                      key={`${o.order.orderId}-${o.order.permId}`}
                      className={singleRowClass}
                      tabIndex={0}
                      data-row-key={rowKey}
                      data-testid={`open-order-row-${rowKey}`}
                      aria-selected={isKeyboardSelected}
                      onClick={() => setKeyboardSelectedKey(rowKey)}
                      onFocus={() => setKeyboardSelectedKey(rowKey)}
                    >
                      {selectCell}
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
                        {intent !== "UNKNOWN" && (
                          <span className="order-intent-pill">{intent}</span>
                        )}
                        {isPending && <Loader2 size={12} className="cancel-spinner" />}
                      </td>
                      <td>
                        <span className={`pill ${o.order.action === "BUY" ? "accum" : "distrib"}`}>
                          {o.order.action}
                        </span>
                      </td>
                      {orderColumns.orderType && <td>{o.order.orderType}</td>}
                      {orderColumns.totalQuantity && (
                        <td className="right">{formatFillQuantity(o.order)}</td>
                      )}
                      {orderColumns.limitPrice && (
                        <td className="right">
                          {isPendingModify && o.order.orderType === "STP LMT" ? (
                            <span className="status-modifying">Modifying...</span>
                          ) : (
                            o.order.limitPrice != null ? fmtPrice(o.order.limitPrice) : "—"
                          )}
                        </td>
                      )}
                      {orderColumns.lastPrice && (
                        <OrderPriceCell price={singleLast} />
                      )}
                      {orderColumns.deltaFill && (
                        <td className="right">
                          {singleDistance ? (
                            <span className={`order-delta order-delta--${singleDistance.urgency}`}>
                              {formatDistanceDelta(singleDistance.delta)}
                            </span>
                          ) : (
                            "--"
                          )}
                        </td>
                      )}
                      {showImplied && orderColumns.implied && (
                        <OrderImpliedCell
                          price={prices ? resolveOrderImpliedValue(o.order, prices, riskFreeRate) : null}
                        />
                      )}
                      {showImplied && orderColumns.implied_mv && (
                        <OrderImpliedMvCell
                          value={prices ? resolveSingleOrderImpliedMv(o.order, prices, riskFreeRate) : null}
                        />
                      )}
                      <td className="open-order-status-cell">
                        <span
                          className={statusPillClass(singleStatus.tone)}
                          title={singleStatus.raw}
                        >
                          {singleStatus.label}
                        </span>
                        <SessionWindowChip session={session} />
                      </td>
                      {orderColumns.tif && <td>{o.order.tif}</td>}
                      <td className="actions-cell">
                        {isPending ? (
                          <span className="cancel-pending-label">PENDING</span>
                        ) : (
                          <>
                            <button
                              className="btn-order-action btn-modify"
                              disabled={!canModify(o.order)}
                              title={canModify(o.order) ? "Modify limit price" : MODIFY_DISABLED_TITLE}
                              onClick={() => setModifyTarget({
                                modalOrder: o.order,
                                requestOrder: o.order,
                              })}
                            >
                              MODIFY
                            </button>
                            <button
                              className="btn-order-action btn-cancel"
                              onClick={() => {
                                setCancelCombo(null);
                                setCancelTarget(o.order);
                              }}
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
            </div>
          )}
        </div>
      </div>

      <div className="section" id="orders-executed">
        <div className="section-header">
          <h2 className="section-title">
            <CheckCircle2 size={14} />
            Today&apos;s Executed Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Today's Executed Orders"]} />
          </h2>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <TableSearch query={execFilter.query} setQuery={execFilter.setQuery} placeholder="Filter fills..." resultCount={execFilter.filtered.length} totalCount={positionGroups.length} />
            <span className="pill neutral">{positionGroups.length} {positionGroups.length === 1 ? "POSITION" : "POSITIONS"}</span>
          </div>
        </div>
        <div className="section-body">
          {positionGroups.length === 0 ? (
            <SectionEmptyState
              icon={History}
              headline="No fills today"
              secondary="Executions during today's session will appear here as orders fill."
              testId="today-executed-empty"
            />
          ) : showMobileOrders ? (
            <MobileExecutedList groups={execGroupSort.sorted} />
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: "24px" }}></th>
                  <SortTh<ExecGroupKey> label="Position" sortKey="position" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Action" sortKey="action" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Quantity" sortKey="quantity" className="right" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Net Price" sortKey="netPrice" className="right" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Commission" sortKey="commission" className="right" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Realized P&L" sortKey="pnl" className="right" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <SortTh<ExecGroupKey> label="Time" sortKey="time" activeKey={execGroupSort.sort.key} direction={execGroupSort.sort.direction} onToggle={execGroupSort.toggle} />
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {execGroupSort.sorted.map((group) => {
                  const isExpanded = expandedGroups.has(group.id);
                  const isCancelled = group.fills[0]?.side === "CANCELLED";
                  const shareData = group.isClosing && group.totalPnL != null
                    ? positionGroupShareData(group, positionGroups, portfolio?.positions, portfolio?.trade_log_dates, portfolio?.contract_open_dates)
                    : null;
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
                            // Same Return on Risk % the share card carries — single source of truth.
                            const pct = shareData?.pnlPct ?? closedGroupReturnPct(group);
                            return `${group.totalPnL >= 0 ? "+" : ""}${fmtPrice(group.totalPnL)}${pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)` : ""}`;
                          })() : "—"}
                        </td>
                        <td>{formatExecutedFillTime(group.time)}</td>
                        <td>
                          {shareData != null && (
                            <SharePnlButton data={shareData} />
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
                            <td style={{ color: "var(--text-secondary)" }}>{formatExecutedFillTime(e.time)}</td>
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

      <HistoricalTradesSection defaultExpanded={openOrderRows.length === 0} />

      <CashFlowsSection />
    </>
  );
}

/* ─── Historical Trades (Flex Query) ───────────────────── */

const BLOTTER_STALE_THRESHOLD_DAYS = 1;

type BlotterSortKey = "date" | "symbol" | "contract_desc" | "sec_type" | "status" | "net_quantity" | "total_commission" | "realized_pnl" | "cost_basis" | "proceeds";

function getTradeDate(item: BlotterTrade): string {
  if (item.executions.length === 0) return "";
  return item.executions[item.executions.length - 1].time;
}

function blotterStalenessAgeDays(asOf: string | undefined | null): number | null {
  if (!asOf) return null;
  const asOfTime = Date.parse(asOf.length === 10 ? `${asOf}T12:00:00` : asOf);
  if (Number.isNaN(asOfTime)) return null;
  const diffMs = Date.now() - asOfTime;
  if (diffMs <= 0) return 0;
  return Math.floor(diffMs / (24 * 60 * 60 * 1000));
}

/** Format blotter as_of without UTC day-shift for date-only strings. */
function formatBlotterAsOf(asOf: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(asOf)) {
    const [y, m, d] = asOf.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString();
  }
  const t = Date.parse(asOf);
  if (Number.isNaN(t)) return asOf;
  return new Date(t).toLocaleDateString();
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

export function HistoricalTradesSection({
  defaultExpanded = true,
}: {
  defaultExpanded?: boolean;
} = {}) {
  const { data, loading, syncing, error, syncNow } = useBlotter(true);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<HistoricalPageSize>(() => {
    if (typeof window === "undefined") return 15;
    return parseHistoricalPageSize(window.localStorage.getItem(HISTORICAL_PAGE_SIZE_KEY));
  });
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { isMobile, hasMounted } = useViewport();
  const showMobileBlotter = isMobile && hasMounted;
  const stopToggle = (e: React.SyntheticEvent) => e.stopPropagation();

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

  const extractSearchText = useCallback((item: BlotterTrade) => {
    const latestExecTime = getTradeDate(item);
    return `${item.symbol} ${item.contract_desc} ${item.sec_type} ${item.is_closed ? "closed" : "open"} ${latestExecTime}`;
  }, []);

  const { filtered, query, setQuery } = useTableFilter(allTrades, extractSearchText);
  const { sorted, sort, toggle } = useSort(filtered, blotterExtract);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = sorted.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const showingLabel = formatShowingRange(safePage, pageSize, sorted.length);

  const setHistoricalPageSize = useCallback((next: HistoricalPageSize) => {
    setPageSize(next);
    setPage(0);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(HISTORICAL_PAGE_SIZE_KEY, String(next));
    }
  }, []);

  // Reset page when data or filter changes
  useEffect(() => { setPage(0); }, [data, query]);

  const totalCount = allTrades.length;
  const hasData = data && (data.as_of || totalCount > 0);
  const stalenessAgeDays = blotterStalenessAgeDays(data?.as_of);
  const isStale = stalenessAgeDays !== null && stalenessAgeDays > BLOTTER_STALE_THRESHOLD_DAYS;

  return (
    <div className="section" id="orders-historical" data-testid="historical-trades-section">
      <div
        className="section-header cash-flows-header"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls="historical-trades-body"
        data-testid="historical-trades-toggle"
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
        style={{ cursor: "pointer", userSelect: "none" }}
      >
        <h2 className="section-title">
          <ChevronDown
            size={12}
            style={{
              transform: expanded ? "rotate(0deg)" : "rotate(-90deg)",
              transition: "transform 150ms ease",
            }}
          />
          <ClipboardList size={14} />
          Historical Trades (30 Days)
          <InfoTooltip text={SECTION_TOOLTIPS["Historical Trades (30 Days)"]} />
        </h2>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {data?.as_of && (
            <span className="report-meta" style={{ margin: 0, padding: 0, border: "none" }}>
              {formatBlotterAsOf(data.as_of)}
            </span>
          )}
          {isStale && stalenessAgeDays !== null && (
            <span
              className="pill bearish"
              title="Blotter has not been refreshed from IB Flex for more than a day. Click Refresh to pull the latest trades."
            >
              STALE · {stalenessAgeDays}d
            </span>
          )}
          {allTrades.length > 0 ? (
            <span onClick={stopToggle} onKeyDown={stopToggle}>
              <TableSearch
                query={query}
                setQuery={setQuery}
                placeholder="Filter historical trades..."
                resultCount={filtered.length}
                totalCount={allTrades.length}
              />
            </span>
          ) : null}
          <span className="pill neutral">{totalCount} TRADES</span>
          <button
            className="sync-button"
            disabled={syncing}
            onClick={(e) => {
              e.stopPropagation();
              syncNow();
            }}
          >
            {syncing ? <><Loader2 size={12} className="spin" /> Syncing...</> : "Refresh"}
          </button>
        </div>
      </div>
      {expanded && (
      <div id="historical-trades-body" className="section-body">
        {error && (
          <SectionEmptyState
            icon={TriangleAlert}
            tone="danger"
            headline="Couldn't load historical trades"
            secondary={error}
            action={{ label: syncing ? "Refreshing…" : "Refresh", onClick: syncNow, disabled: syncing }}
            testId="historical-trades-error"
          />
        )}
        {loading && <div className="p-6"><SpectralLoader label="Loading historical trades" /></div>}
        {!loading && !error && totalCount === 0 && (
          <SectionEmptyState
            icon={History}
            headline="No historical trades"
            secondary={
              hasData
                ? "No fills in the last 30 days. Click Refresh to pull again from IB Flex."
                : "Pull the last 30 days of IB Flex trades to populate this list."
            }
            action={{ label: syncing ? "Refreshing…" : "Refresh from IB", onClick: syncNow, disabled: syncing }}
            testId="historical-trades-empty"
          />
        )}
        {!loading && pageRows.length > 0 && showMobileBlotter && (
          <>
            <div className="historical-page-controls" data-testid="historical-page-controls">
              <span className="page-meta" data-testid="historical-showing-range">{showingLabel}</span>
              <label className="historical-page-size">
                <span className="historical-page-size__label">Rows</span>
                <select
                  aria-label="Historical trades page size"
                  data-testid="historical-page-size"
                  value={pageSize}
                  onChange={(e) => setHistoricalPageSize(parseHistoricalPageSize(e.target.value))}
                >
                  {HISTORICAL_PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </label>
            </div>
            <MobileBlotterList trades={pageRows} />
            {totalPages > 1 && (
              <div className="pagination" style={{ marginTop: 8 }}>
                <button
                  className="page-button"
                  disabled={safePage === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Prev
                </button>
                <span className="page-meta">
                  Page {safePage + 1} of {totalPages}
                </span>
                <button
                  className="page-button"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
        {!loading && pageRows.length > 0 && !showMobileBlotter && (
          <>
            <div className="historical-page-controls" data-testid="historical-page-controls">
              <span className="page-meta" data-testid="historical-showing-range">{showingLabel}</span>
              <label className="historical-page-size">
                <span className="historical-page-size__label">Rows</span>
                <select
                  aria-label="Historical trades page size"
                  data-testid="historical-page-size"
                  value={pageSize}
                  onChange={(e) => setHistoricalPageSize(parseHistoricalPageSize(e.target.value))}
                >
                  {HISTORICAL_PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </label>
            </div>
            <table>
              <thead>
                <tr>
                  <SortTh<BlotterSortKey> label="Date" sortKey="date" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Symbol" sortKey="symbol" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Description" sortKey="contract_desc" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Type" sortKey="sec_type" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Status" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Qty" sortKey="net_quantity" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Commission" sortKey="total_commission" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Realized P&L" sortKey="realized_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Cost Basis" sortKey="cost_basis" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Proceeds" sortKey="proceeds" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((t, i) => {
                  const realizedBasis = t.realized_cost_basis != null ? Math.abs(t.realized_cost_basis) : (t.cost_basis != null ? Math.abs(t.cost_basis) : 0);
                  const realizedPct = t.realized_pnl != null && realizedBasis > 0 ? (t.realized_pnl / realizedBasis) * 100 : null;
                  const partiallyRealized = !t.is_closed && (t.realized_quantity ?? 0) > 0;
                  const realizedLabel = partiallyRealized
                    ? `${t.realized_quantity} ${t.net_quantity >= 0 ? "sold" : "covered"}`
                    : null;

                  return (
                    <tr key={`${t.symbol}-${t.contract_desc}-${i}`}>
                      <td>{getTradeDate(t) ? formatTradeDate(getTradeDate(t)) : "—"}</td>
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
                      {/* Colour only a KNOWN value. `(t.realized_pnl ?? 0) >= 0`
                          painted a null P&L green while the text correctly
                          rendered `---`, so on a blotter scanned by colour an
                          unknown close was indistinguishable from a
                          profitable one. R-248. */}
                      <td className={`right ${t.realized_pnl == null ? "" : t.realized_pnl >= 0 ? "positive" : "negative"}`}>
                        {t.realized_pnl != null ? (
                          <>
                            {t.realized_pnl >= 0 ? "+" : ""}{fmtPrice(t.realized_pnl)}
                            {realizedPct != null ? ` (${realizedPct >= 0 ? "+" : ""}${realizedPct.toFixed(1)}%)` : ""}
                            {realizedLabel ? ` · ${realizedLabel}` : ""}
                          </>
                        ) : "---"}
                      </td>
                      <td className="right">{t.cost_basis != null ? fmtPrice(t.cost_basis) : "---"}</td>
                      <td className="right">{t.proceeds != null ? fmtPrice(t.proceeds) : "---"}</td>
                      <td>
                        {t.is_closed && <SharePnlButton data={blotterShareData(t)} />}
                      </td>
                    </tr>
                  );
                })}
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
      )}
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
  depths?: Record<string, DepthBook>;
  tape?: Record<string, Trade[]>;
  tickerParam?: string;
  theme?: "dark" | "light";
  marketState?: MarketState;
};

function WorkspaceSections({ section, portfolio, portfolioLastSync, orders, prices, depths, tape, tickerParam, theme, marketState }: WorkspaceSectionsProps) {
  switch (section) {
    case "dashboard":
      return null;
    case "flow-analysis":
      return <FlowSections tickerParam={tickerParam} />;
    case "options":
      return <OptionsWorkspacePanel symbol={tickerParam} />;
    case "portfolio":
      // WorkspaceShell owns the isolated portfolio chunk. Keeping the branch
      // explicit makes this switch exhaustive without pulling that surface
      // into every non-portfolio workspace bundle.
      return null;
    case "performance":
      return <PerformancePanel portfolioLastSync={portfolioLastSync} marketState={marketState} />;
    case "orders":
      return <OrdersSections orders={orders ?? null} prices={prices} portfolio={portfolio} />;
    case "scanner":
      return <ScannerSections />;
    case "discover":
      return <ScannerSections defaultMode="discover" />;
    case "journal":
      return <JournalSections />;
    case "regime":
      return <RegimePanel prices={prices ?? {}} marketState={marketState} />;
    case "cta":
      return <CtaPage />;
    case "alerts":
      return (
        <div className="alerts-shell">
          <AlertsPanel />
        </div>
      );
    case "workflow":
      return <WorkflowComposer />;
    case "admin":
      return <AdminWorkspace />;
    case "preferences":
      return <PreferencesSection />;
    case "profile":
      return <ProfileContent prices={prices} />;
    case "watchlist":
      return <WatchlistContent prices={prices} portfolio={portfolio ?? null} orders={orders ?? null} />;
    case "ticker-detail":
      return tickerParam ? (
        <TickerWorkspace ticker={tickerParam} theme={theme ?? "dark"} depths={depths} tape={tape} />
      ) : null;
    default:
      return <FlowSections tickerParam={tickerParam} />;
  }
}

// Memo so shell price ticks do not re-render scanner/discover (and other
// sections that omit prices). WorkspaceShell passes prices only when needed.
export default memo(WorkspaceSections);
