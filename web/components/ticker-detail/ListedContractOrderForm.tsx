"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { OrderRiskGate, type OrderRiskInput, type OrderRiskState } from "@/lib/order";
import type { PortfolioData } from "@/lib/types";

export type OrderAction = "BUY" | "SELL";
export type OrderTif = "DAY" | "GTC";

/**
 * Numeric form values shared across listed-contract order surfaces. Adapters
 * derive notional / risk input / submit payload from these plus their own
 * selected-contract state.
 */
export interface ListedOrderFormValues {
  action: OrderAction;
  quantity: string;
  limitPrice: string;
  tif: OrderTif;
}

export interface ListedContractOrderFormProps {
  /** Eyebrow row content (symbol + venue metadata). */
  eyebrow: ReactNode;
  /**
   * Contract-selector slot — the expiry / strike / right `<select>` tree that
   * differs between futures (single expiry dropdown) and index options
   * (expiry → right toggle → strike cascade). Rendered above the BUY/SELL
   * toggle.
   */
  contractSelector: ReactNode;
  /** Multiplier used for the notional summary row. */
  multiplier: number;
  /** Display string for the multiplier summary row (e.g. "1,000" or "100"). */
  multiplierDisplay: string;
  /** Label for the notional summary row. */
  notionalLabel: string;
  /** Label for the limit-price input. */
  limitPriceLabel: string;
  /** Step for the limit-price input. */
  limitPriceStep: number;
  /**
   * Builds the chokepoint risk input from the current form values. Returns
   * `null` when the order is incomplete (no contract / non-positive
   * price-or-qty); the gate then renders nothing.
   */
  buildRiskInput: (values: ListedOrderFormValues) => OrderRiskInput | null;
  /** Live portfolio snapshot routed into `<OrderRiskGate>`. */
  portfolio: PortfolioData | null | undefined;
  /** Telemetry surface tag for `<OrderRiskGate>`. */
  surface: string;
  /**
   * Validates the form and builds the `/api/orders/place` POST body. Return a
   * `{ error }` to surface a validation message, or `{ payload, successText }`
   * to submit. `payload` is sent verbatim (preserving type:future vs
   * type:option shape); `successText` renders on a 2xx response.
   */
  buildSubmit: (
    values: ListedOrderFormValues,
  ) => { error: string } | { payload: Record<string, unknown>; successText: string };
  /**
   * Submit button label. Pass a function to make it action-aware — the form
   * owns the BUY/SELL toggle, so a static string cannot reflect the current
   * action (e.g. "BUY VIXM6" vs "SELL VIXM6").
   */
  submitLabel: string | ((action: OrderAction) => string);
  /**
   * Whether the submit button is disabled beyond the in-flight state (e.g. no
   * contract selected yet).
   */
  submitDisabled: boolean;
}

/**
 * Shared presentational order form for listed instruments (futures + index
 * options). Owns the eyebrow, BUY/SELL toggle, quantity / limit / TIF inputs,
 * notional summary rows, the `<OrderRiskGate>` slot, the submit button, and
 * the error / success rows — all using the existing `futures-form-*` classes
 * so visual output and E2E selectors stay green.
 *
 * The differing pieces (contract selector, multiplier, labels, risk input,
 * submit payload) are injected via props. The `/api/orders/place` POST
 * behaviour is identical to the per-form implementations it replaced.
 */
export function ListedContractOrderForm({
  eyebrow,
  contractSelector,
  multiplier,
  multiplierDisplay,
  notionalLabel,
  limitPriceLabel,
  limitPriceStep,
  buildRiskInput,
  portfolio,
  surface,
  buildSubmit,
  submitLabel,
  submitDisabled,
}: ListedContractOrderFormProps) {
  const [action, setAction] = useState<OrderAction>("BUY");
  const [quantity, setQuantity] = useState<string>("1");
  const [limitPrice, setLimitPrice] = useState<string>("");
  const [tif, setTif] = useState<OrderTif>("DAY");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitOk, setSubmitOk] = useState<string | null>(null);
  const [riskSnapshot, setRiskSnapshot] = useState<{
    input: OrderRiskInput;
    state: OrderRiskState;
  } | null>(null);

  const values = useMemo<ListedOrderFormValues>(
    () => ({ action, quantity, limitPrice, tif }),
    [action, quantity, limitPrice, tif],
  );

  const notional = (() => {
    const price = parseFloat(limitPrice);
    const qty = parseInt(quantity, 10);
    if (!Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) return null;
    return Math.abs(price * qty * multiplier);
  })();

  const riskInput = useMemo(() => buildRiskInput(values), [buildRiskInput, values]);
  const captureRiskState = useCallback((state: OrderRiskState | null) => {
    if (riskInput && state) setRiskSnapshot({ input: riskInput, state });
  }, [riskInput]);
  const riskPermitsSubmit =
    riskInput != null &&
    riskSnapshot?.input === riskInput &&
    riskSnapshot.state.okToSubmit === true;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setSubmitOk(null);

    if (!riskInput || !riskPermitsSubmit) {
      setSubmitError("Portfolio risk is still resolving. Review again before submitting.");
      return;
    }

    const result = buildSubmit(values);
    if ("error" in result) {
      setSubmitError(result.error);
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result.payload),
        cache: "no-store",
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        throw new Error((body.error as string) ?? `Order failed (${res.status})`);
      }
      setSubmitOk(result.successText);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Order failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="futures-order-form" onSubmit={handleSubmit}>
      <div
        className="futures-form-eyebrow"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "10px",
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "var(--signal-core-text)",
          marginBottom: "12px",
        }}
      >
        {eyebrow}
      </div>

      {contractSelector}

      <div className="futures-form-row futures-form-action-row">
        <button
          type="button"
          onClick={() => setAction("BUY")}
          className={`futures-form-action${action === "BUY" ? " futures-form-action--active" : ""}`}
        >
          BUY
        </button>
        <button
          type="button"
          onClick={() => setAction("SELL")}
          className={`futures-form-action${action === "SELL" ? " futures-form-action--active" : ""}`}
        >
          SELL
        </button>
      </div>

      <label className="futures-form-row">
        <span className="futures-form-label">Quantity (contracts)</span>
        <input
          type="number"
          min={1}
          step={1}
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          className="futures-form-input"
          placeholder="Contracts"
        />
      </label>

      <label className="futures-form-row">
        <span className="futures-form-label">{limitPriceLabel}</span>
        <input
          type="number"
          step={limitPriceStep}
          value={limitPrice}
          onChange={(e) => setLimitPrice(e.target.value)}
          className="futures-form-input"
          placeholder="0.00"
        />
      </label>

      <label className="futures-form-row">
        <span className="futures-form-label">TIF</span>
        <select
          value={tif}
          onChange={(e) => setTif(e.target.value as OrderTif)}
          className="futures-form-select"
        >
          <option value="DAY">DAY</option>
          <option value="GTC">GTC</option>
        </select>
      </label>

      <div className="futures-form-summary">
        <div className="futures-form-summary-row">
          <span>Multiplier</span>
          <span>{multiplierDisplay}</span>
        </div>
        <div className="futures-form-summary-row">
          <span>{notionalLabel}</span>
          <span>
            {notional == null
              ? "---"
              : `$${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
          </span>
        </div>
      </div>

      {/* Risk math owned by `<OrderRiskGate>`. SHORT futures / naked SELL CALL
          surface UNBOUNDED + Gate-1 warning automatically. */}
      <OrderRiskGate
        input={riskInput}
        portfolio={portfolio}
        surface={surface}
        variant="info"
        onState={captureRiskState}
      />

      <button
        type="submit"
        disabled={submitting || submitDisabled || !riskPermitsSubmit}
        className="futures-form-submit"
      >
        {submitting ? "Submitting…" : typeof submitLabel === "function" ? submitLabel(action) : submitLabel}
      </button>

      {submitError && <div className="futures-form-error">{submitError}</div>}
      {submitOk && <div className="futures-form-success">{submitOk}</div>}
    </form>
  );
}
