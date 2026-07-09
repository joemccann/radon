"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import {
  formatExpiry,
  daysToExpiry,
  type OrderLeg,
  normalizeComboOrder,
  computeNetOptionQuote,
  computeNetPrice,
  detectStructure,
  ALL_STRIKES,
} from "@/lib/optionsChainUtils";
import type { SideFilter } from "@/lib/useChainUrlState";
import BottomSheet from "./BottomSheet";
import MobileOrderTicket from "./MobileOrderTicket";
import SpectralLoader from "@/components/SpectralLoader";

type Strike = {
  strike: number;
  callKey: string;
  putKey: string;
};

/** Strikes-per-side options — mirrors the desktop chain selector. */
const STRIKE_OPTIONS: { value: number; label: string }[] = [
  { value: 10, label: "±10" },
  { value: 15, label: "±15" },
  { value: 25, label: "±25" },
  { value: 50, label: "±50" },
  { value: 100, label: "±100" },
  { value: ALL_STRIKES, label: "All" },
];

const SIDE_OPTIONS: { value: SideFilter; label: string }[] = [
  { value: "both", label: "ALL" },
  { value: "calls", label: "CALLS" },
  { value: "puts", label: "PUTS" },
];

type MobileChainLadderProps = {
  ticker: string;
  expirations: string[];
  selectedExpiry: string | null;
  onSelectExpiry: (expiry: string) => void;
  visibleStrikes: Strike[];
  atmStrike: number | null;
  prices: Record<string, PriceData>;
  currentPrice: number | null;
  loading?: boolean;
  /** Calls / puts / both filter — mirrors desktop, deep-linked via the URL. */
  sideFilter: SideFilter;
  onSideFilterChange: (side: SideFilter) => void;
  /** Strikes-per-side window — mirrors desktop, deep-linked via the URL. */
  strikesPerSide: number;
  onStrikesPerSideChange: (strikes: number) => void;
  orderLegs?: OrderLeg[];
  riskFreeRate?: number;
  onAddLeg?: (strike: number, right: "C" | "P", action: "BUY" | "SELL") => void;
  onRemoveLeg?: (id: string) => void;
  onUpdateLeg?: (id: string, updates: Partial<OrderLeg>) => void;
  onClearLegs?: () => void;
  /**
   * Live portfolio snapshot. Threaded down so the mobile ticket's risk gate
   * can fold in held-LONG coverage for SELL legs and stock-backed covered
   * calls — same contract as desktop chain.
   */
  portfolio?: PortfolioData | null;
};

type SelectedCell = {
  strike: number;
  right: "C" | "P";
  data: PriceData | null;
};

function fmtIv(iv: number | null | undefined): string {
  if (iv == null || !Number.isFinite(iv)) return "—";
  return `${(iv * 100).toFixed(1)}%`;
}

function fmtOi(oi: number | null | undefined): string {
  if (oi == null || !Number.isFinite(oi)) return "—";
  if (oi >= 10000) return `${(oi / 1000).toFixed(0)}k`;
  if (oi >= 1000) return `${(oi / 1000).toFixed(1)}k`;
  return String(oi);
}

function fmtLast(last: number | null | undefined): string {
  if (last == null || !Number.isFinite(last) || last <= 0) return "—";
  return fmtPrice(last);
}

function fmtBidAsk(bid: number | null | undefined, ask: number | null | undefined): string {
  const b = bid != null && Number.isFinite(bid) ? fmtPrice(bid) : "—";
  const a = ask != null && Number.isFinite(ask) ? fmtPrice(ask) : "—";
  return `${b} x ${a}`;
}

function fmtGreek(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** F1: long-press fires the quick-add popover at this dwell; a pointer drag past
 *  the move threshold (a scroll) cancels it. */
const LONG_PRESS_MS = 480;
const LONG_PRESS_MOVE_PX = 10;

/** One option side rendered inside a ladder row. Compact in the two-sided
 *  layout; surfaces Δ and Vol inline when a single side has the full width. */
function SideCell({
  right,
  strike,
  data,
  align,
  expanded,
  selectedAction,
  onSelect,
  onAddLeg,
}: {
  right: "C" | "P";
  strike: number;
  data: PriceData | null;
  align: "left" | "right";
  expanded: boolean;
  /** BUY / SELL when this contract is an active order leg — drives the
   *  selection tint (P5). null = not in the pending order. */
  selectedAction: "BUY" | "SELL" | null;
  onSelect: () => void;
  /** F1: quick-add without the detail sheet. When absent, long-press is a
   *  no-op (the ladder isn't in order-building mode). */
  onAddLeg?: (strike: number, right: "C" | "P", action: "BUY" | "SELL") => void;
}) {
  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const pressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressOriginRef = useRef<{ x: number; y: number } | null>(null);
  // Set when a long-press fires so the click that follows pointerup doesn't
  // also open the detail sheet.
  const longPressFiredRef = useRef(false);
  // Timestamp of the moment the popover opened. The press-release that opens it
  // synthesises a trailing `click` under the finger, which would land on the
  // dismiss backdrop and close the popover the instant it appears. Ignore
  // backdrop dismissals within this window so the popover survives its own
  // opening gesture; a later tap-outside still closes it.
  const openedAtRef = useRef(0);

  const cancelPress = () => {
    if (pressTimerRef.current != null) {
      clearTimeout(pressTimerRef.current);
      pressTimerRef.current = null;
    }
    pressOriginRef.current = null;
  };

  const handlePointerDown = (event: ReactPointerEvent) => {
    if (!onAddLeg) return;
    longPressFiredRef.current = false;
    pressOriginRef.current = { x: event.clientX, y: event.clientY };
    pressTimerRef.current = setTimeout(() => {
      longPressFiredRef.current = true;
      openedAtRef.current = Date.now();
      navigator.vibrate?.(10);
      setQuickAddOpen(true);
      cancelPress();
    }, LONG_PRESS_MS);
  };

  const handlePointerMove = (event: ReactPointerEvent) => {
    const origin = pressOriginRef.current;
    if (!origin) return;
    if (
      Math.abs(event.clientX - origin.x) > LONG_PRESS_MOVE_PX ||
      Math.abs(event.clientY - origin.y) > LONG_PRESS_MOVE_PX
    ) {
      cancelPress();
    }
  };

  const handleClick = () => {
    // Suppress the click synthesised after a long-press so the detail sheet
    // doesn't open on top of the quick-add popover.
    if (longPressFiredRef.current) {
      longPressFiredRef.current = false;
      return;
    }
    onSelect();
  };

  const handleKeyDown = (event: ReactKeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };

  const quickAdd = (action: "BUY" | "SELL") => {
    onAddLeg?.(strike, right, action);
    setQuickAddOpen(false);
  };

  const selectedClass =
    selectedAction === "BUY"
      ? " mobile-chain__cell--selected-buy"
      : selectedAction === "SELL"
        ? " mobile-chain__cell--selected-sell"
        : "";
  return (
    <div
      role="button"
      tabIndex={0}
      className={`mobile-chain__cell mobile-chain__cell--${right === "C" ? "call" : "put"}${align === "right" ? " mobile-chain__cell--right" : ""}${selectedClass}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={cancelPress}
      onPointerLeave={cancelPress}
      onPointerCancel={cancelPress}
      data-testid={`mobile-chain-${right === "C" ? "call" : "put"}-${strike}`}
      aria-label={`${right === "C" ? "Call" : "Put"} ${strike}`}
      aria-pressed={selectedAction != null}
    >
      <span className="mobile-chain__last">{fmtLast(data?.last)}</span>
      <span className="mobile-chain__bid-ask">{fmtBidAsk(data?.bid, data?.ask)}</span>
      <span className="mobile-chain__meta">
        <span>IV {fmtIv(data?.impliedVol)}</span>
        {expanded ? <span>Δ {fmtGreek(data?.delta, 2)}</span> : null}
        {expanded ? <span>V {fmtOi(data?.volume)}</span> : null}
        <span>OI {fmtOi(data?.avgVolume)}</span>
      </span>

      {quickAddOpen ? (
        <>
          {/* Dismiss backdrop — stops propagation so tapping outside neither
              opens the detail sheet nor re-triggers the cell. */}
          <div
            className="mobile-chain__quickadd-backdrop"
            onClick={(e) => {
              e.stopPropagation();
              // Swallow the trailing click from the opening long-press so the
              // popover does not dismiss itself the moment it appears.
              if (Date.now() - openedAtRef.current < 400) return;
              setQuickAddOpen(false);
            }}
            onPointerDown={(e) => e.stopPropagation()}
            aria-hidden
          />
          <div
            className="mobile-chain__quickadd"
            role="menu"
            aria-label={`Quick add ${right === "C" ? "call" : "put"} ${strike}`}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setQuickAddOpen(false);
              }
            }}
            data-testid={`mobile-chain-quickadd-${right === "C" ? "call" : "put"}-${strike}`}
          >
            <button
              type="button"
              role="menuitem"
              autoFocus
              className="mobile-chain__quickadd-buy"
              onClick={() => quickAdd("BUY")}
              data-testid={`mobile-chain-quickadd-${right === "C" ? "call" : "put"}-${strike}-buy`}
            >
              BUY
            </button>
            <button
              type="button"
              role="menuitem"
              className="mobile-chain__quickadd-sell"
              onClick={() => quickAdd("SELL")}
              data-testid={`mobile-chain-quickadd-${right === "C" ? "call" : "put"}-${strike}-sell`}
            >
              SELL
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default function MobileChainLadder({
  ticker,
  expirations,
  selectedExpiry,
  onSelectExpiry,
  visibleStrikes,
  atmStrike,
  prices,
  currentPrice,
  loading,
  sideFilter,
  onSideFilterChange,
  strikesPerSide,
  onStrikesPerSideChange,
  orderLegs = [],
  riskFreeRate,
  onAddLeg,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
  portfolio = null,
}: MobileChainLadderProps) {
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [ticketOpen, setTicketOpen] = useState(false);
  const ladderRef = useRef<HTMLDivElement>(null);
  const atmRowRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to ATM strike on mount and when expiry/strikes change.
  // scrollIntoView is used for the initial snap; on subsequent expiry
  // changes the manual offsetTop path centers the row within the scroller.
  useEffect(() => {
    if (!atmRowRef.current || !ladderRef.current) return;
    const atm = atmRowRef.current;
    const wrapper = ladderRef.current;
    // Center the ATM row WITHIN the ladder scroller only. scrollIntoView would
    // bubble to scrollable ancestors (the deck body), dragging the sticky
    // controls out of view — measure against the wrapper rect instead.
    const wrapperRect = wrapper.getBoundingClientRect();
    const atmRect = atm.getBoundingClientRect();
    const target =
      wrapper.scrollTop +
      (atmRect.top - wrapperRect.top) +
      atmRect.height / 2 -
      wrapper.clientHeight / 2;
    wrapper.scrollTo({ top: Math.max(0, target), behavior: "instant" });
  }, [selectedExpiry, visibleStrikes.length, atmStrike]);

  // Compute live net mid for the pending strip so the operator can see the
  // current market mid without opening the ticket.
  const pendingNetMid = useMemo(() => {
    if (orderLegs.length === 0) return null;
    const normalized = normalizeComboOrder(orderLegs);
    const quote = computeNetOptionQuote(normalized.legs, prices, ticker);
    if (quote.mid == null) return null;
    return quote.mid;
  }, [orderLegs, prices, ticker]);

  // F2: structure name + signed debit/credit for the pending strip preview.
  // Sign comes from computeNetPrice (positive = debit, negative = credit) —
  // computeNetOptionQuote returns unsigned magnitudes, so it can't tell the
  // direction on its own.
  const pendingStructure = useMemo(
    () => (orderLegs.length > 0 ? detectStructure(orderLegs) : ""),
    [orderLegs],
  );
  const pendingSignedNet = useMemo(() => {
    if (orderLegs.length === 0) return null;
    const normalized = normalizeComboOrder(orderLegs);
    return computeNetPrice(normalized.legs, prices);
  }, [orderLegs, prices]);
  const pendingIsCredit = pendingSignedNet != null ? pendingSignedNet < 0 : null;

  // P5: (strike,right) -> BUY/SELL for the active order legs, so the ladder can
  // tint the cells that are already in the pending order.
  const legActionByKey = useMemo(() => {
    const map = new Map<string, "BUY" | "SELL">();
    for (const leg of orderLegs) {
      map.set(`${leg.strike}_${leg.right}`, leg.action);
    }
    return map;
  }, [orderLegs]);

  const expiryChips = useMemo(() => expirations.slice(0, 24), [expirations]);
  const showCalls = sideFilter !== "puts";
  const showPuts = sideFilter !== "calls";
  const singleSide = showCalls !== showPuts;
  // Grid column template differs by visible side(s): both keeps the centered
  // strike spine; calls-only anchors the strike on the right, puts-only on the
  // left, so the strike column always neighbours its side. Applied to the
  // ladder head and every row so they stay aligned.
  const gridClass = !singleSide ? "mc-grid-both" : showCalls ? "mc-grid-calls" : "mc-grid-puts";

  return (
    <div className="mobile-chain" data-testid="mobile-chain">
      <div className="mobile-chain__header">
        <div className="mobile-chain__control" data-testid="mobile-chain-expiry-control">
          <div className="mobile-chain__control-head">
            <span className="mobile-chain__control-label">EXPIRY</span>
            <span className="mobile-chain__spot">
              {ticker.toUpperCase()}{" "}
              <span className="mobile-chain__spot-val">
                {currentPrice != null ? fmtPrice(currentPrice) : "—"}
              </span>
            </span>
          </div>
          <div className="mobile-chain__expiry-bar" data-testid="mobile-chain-expiry-bar">
            {expiryChips.map((exp) => {
              const active = exp === selectedExpiry;
              return (
                <button
                  key={exp}
                  type="button"
                  className={`mobile-chain__expiry-chip${active ? " mobile-chain__expiry-chip--active" : ""}`}
                  onClick={() => onSelectExpiry(exp)}
                  aria-pressed={active}
                  data-testid={`mobile-chain-expiry-${exp}`}
                >
                  <span className="mobile-chain__expiry-date">{formatExpiry(exp)}</span>
                  <span className="mobile-chain__expiry-dte">{daysToExpiry(exp)}d</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="mobile-chain__filter-row">
          <div
            className="mobile-chain__side-toggle"
            role="group"
            aria-label="Option side"
            data-testid="mobile-chain-side-toggle"
          >
            {SIDE_OPTIONS.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                className={`mobile-chain__side-btn${sideFilter === value ? " mobile-chain__side-btn--active" : ""}`}
                onClick={() => onSideFilterChange(value)}
                aria-pressed={sideFilter === value}
                data-testid={`mobile-chain-side-${value}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="mobile-chain__strikes">
            <span className="mobile-chain__control-label">STRIKES</span>
            <select
              className="mobile-chain__strikes-select"
              value={strikesPerSide}
              onChange={(e) => onStrikesPerSideChange(Number(e.target.value))}
              data-testid="mobile-chain-strikes-select"
            >
              {STRIKE_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className={`mobile-chain__ladder-head ${gridClass}`}>
        {showCalls && <div className="mobile-chain__side-label">CALLS</div>}
        <div className="mobile-chain__strike-label">STRIKE</div>
        {showPuts && <div className="mobile-chain__side-label">PUTS</div>}
      </div>

      {loading ? (
        <div className="mobile-empty-state" data-testid="mobile-chain-loading">
          <SpectralLoader label="Loading chain" />
        </div>
      ) : visibleStrikes.length === 0 ? (
        <div className="mobile-empty-state" data-testid="mobile-chain-empty">
          <span>No strikes for this expiry.</span>
        </div>
      ) : (
        <div className="mobile-chain__ladder" ref={ladderRef} data-testid="mobile-chain-ladder">
          {visibleStrikes.map(({ strike, callKey, putKey }) => {
            const call = prices[callKey] ?? null;
            const put = prices[putKey] ?? null;
            const isAtm = atmStrike != null && strike === atmStrike;
            const rowClass = `mobile-chain__row ${gridClass}${isAtm ? " mobile-chain__row--atm" : ""}`;

            return (
              <div
                key={strike}
                className={rowClass}
                ref={isAtm ? atmRowRef : undefined}
                data-testid={`mobile-chain-row-${strike}`}
              >
                {showCalls && (
                  <SideCell
                    right="C"
                    strike={strike}
                    data={call}
                    align="left"
                    expanded={singleSide}
                    selectedAction={legActionByKey.get(`${strike}_C`) ?? null}
                    onSelect={() => setSelected({ strike, right: "C", data: call })}
                    onAddLeg={onAddLeg}
                  />
                )}

                <div className="mobile-chain__strike">{strike}</div>

                {showPuts && (
                  <SideCell
                    right="P"
                    strike={strike}
                    data={put}
                    align={singleSide ? "left" : "right"}
                    expanded={singleSide}
                    selectedAction={legActionByKey.get(`${strike}_P`) ?? null}
                    onSelect={() => setSelected({ strike, right: "P", data: put })}
                    onAddLeg={onAddLeg}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {orderLegs.length > 0 ? (
        // F2: single thumb bar. Tapping the bar (the primary Review affordance)
        // opens the ticket; the Clear control stops propagation so it discards
        // without opening.
        <div
          role="button"
          tabIndex={0}
          className="mobile-chain__pending-strip"
          onClick={() => setTicketOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setTicketOpen(true);
            }
          }}
          data-testid="mobile-chain-pending-strip"
        >
          <span className="mobile-chain__pending-summary">
            <span className="mobile-chain__pending-structure">
              {pendingStructure || "Order"}
            </span>
            <span className="mobile-chain__pending-detail">
              <span className="mobile-chain__pending-count">
                {orderLegs.length} {orderLegs.length === 1 ? "LEG" : "LEGS"}
              </span>
              {pendingNetMid != null ? (
                <span
                  className={`mobile-chain__pending-net${
                    pendingIsCredit === true
                      ? " mobile-chain__pending-net--credit"
                      : pendingIsCredit === false
                        ? " mobile-chain__pending-net--debit"
                        : ""
                  }`}
                  data-testid="mobile-chain-pending-net"
                >
                  {pendingIsCredit === true ? "CR" : "DB"} {fmtPrice(Math.abs(pendingNetMid))}
                </span>
              ) : null}
            </span>
          </span>
          <span className="mobile-chain__pending-actions">
            <button
              type="button"
              className="mobile-chain__pending-clear"
              onClick={(e) => {
                e.stopPropagation();
                onClearLegs?.();
              }}
              data-testid="mobile-chain-pending-clear"
            >
              Clear
            </button>
            <span className="mobile-chain__pending-action">Review &rarr;</span>
          </span>
        </div>
      ) : null}

      <MobileOrderTicket
        open={ticketOpen && orderLegs.length > 0}
        ticker={ticker}
        legs={orderLegs}
        prices={prices}
        spot={currentPrice}
        riskFreeRate={riskFreeRate}
        portfolio={portfolio}
        onClose={() => setTicketOpen(false)}
        onRemoveLeg={(id) => onRemoveLeg?.(id)}
        onUpdateLeg={(id, updates) => onUpdateLeg?.(id, updates)}
        onClearLegs={() => onClearLegs?.()}
      />

      {selected ? (
        <BottomSheet
          open
          onClose={() => setSelected(null)}
          title={`${ticker.toUpperCase()} ${selected.strike} ${selected.right === "C" ? "Call" : "Put"}`}
          testId="mobile-chain-detail-sheet"
          footer={
            onAddLeg ? (
              <div className="mobile-chain__detail-actions">
                <button
                  type="button"
                  className="mobile-chain__detail-buy"
                  onClick={() => {
                    onAddLeg(selected.strike, selected.right, "BUY");
                    setSelected(null);
                  }}
                  data-testid="mobile-chain-detail-buy"
                >
                  BUY
                </button>
                <button
                  type="button"
                  className="mobile-chain__detail-sell"
                  onClick={() => {
                    onAddLeg(selected.strike, selected.right, "SELL");
                    setSelected(null);
                  }}
                  data-testid="mobile-chain-detail-sell"
                >
                  SELL
                </button>
              </div>
            ) : null
          }
        >
          <div className="mobile-chain__detail">
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Last</span>
              <span className="mobile-chain__detail-value">{fmtLast(selected.data?.last)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Bid / Ask</span>
              <span className="mobile-chain__detail-value">
                {selected.data?.bid != null ? fmtPrice(selected.data.bid) : "—"} /{" "}
                {selected.data?.ask != null ? fmtPrice(selected.data.ask) : "—"}
              </span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Bid Size / Ask Size</span>
              <span className="mobile-chain__detail-value">
                {selected.data?.bidSize ?? "—"} / {selected.data?.askSize ?? "—"}
              </span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">IV</span>
              <span className="mobile-chain__detail-value">{fmtIv(selected.data?.impliedVol)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Delta</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.delta)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Gamma</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.gamma, 4)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Theta</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.theta)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Vega</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.vega)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Volume</span>
              <span className="mobile-chain__detail-value">{fmtOi(selected.data?.volume)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Avg Volume</span>
              <span className="mobile-chain__detail-value">{fmtOi(selected.data?.avgVolume)}</span>
            </div>
          </div>
        </BottomSheet>
      ) : null}
    </div>
  );
}
