"use client";

import OrderErrorBanner from "@/components/OrderErrorBanner";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import { OrderRiskGate, type OrderRiskInput, type OrderRiskState } from "@/lib/order";
import type { PriceData } from "@/lib/pricesProtocol";
import { placeOrderFeedback, type PlaceOrderFeedback } from "@/lib/orders/placeOrderFeedback";
import { canonicalPayloadKey, useArmedConfirm } from "@/lib/orders/useArmedConfirm";
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

const PLACE_URL = "/api/orders/place";

/** Form values bound to the contract they were entered for. */
type ContractForm = ListedOrderFormValues & { contract: string | null };

function blankForm(contract: string | null): ContractForm {
  return { contract, action: "BUY", quantity: "1", limitPrice: "", tif: "DAY" };
}

export interface ListedContractOrderFormProps {
  /**
   * Identity of the selected contract (conId). The action / quantity / limit
   * / TIF the operator typed belong to that contract: a different key starts
   * a blank form, so an old limit can never go out on a new contract.
   */
  contractKey: string | null;
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
  /**
   * Live quote for the contract being traded — the SELECTED future / index
   * option, never the cash underlying. Adapters that cannot name a price key
   * for the listed contract omit it and the panel says "No real-time data"
   * rather than quoting a different instrument.
   */
  priceData?: PriceData | null;
  /** Instrument label rendered above the quote fields (e.g. "VIX 2026-09-16 $20 C"). */
  quoteLabel?: string;
  /**
   * Disclosure rendered OUTSIDE the quote panel when the quote shown is not
   * the traded contract's own book. The `Index` suffix in `quoteLabel` sat
   * inside the panel the numbers were in, which is not a disclosure the
   * operator reads while typing a limit into the input below it. R-254.
   */
  quoteProvenance?: string;
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
  contractKey,
  eyebrow,
  contractSelector,
  multiplier,
  multiplierDisplay,
  notionalLabel,
  limitPriceLabel,
  limitPriceStep,
  priceData,
  quoteLabel,
  quoteProvenance,
  buildRiskInput,
  portfolio,
  surface,
  buildSubmit,
  submitLabel,
  submitDisabled,
}: ListedContractOrderFormProps) {
  const [form, setForm] = useState<ContractForm>(() => blankForm(contractKey));
  // Values typed for another contract are never shown or sent.
  const current = form.contract === contractKey ? form : blankForm(contractKey);
  const { action, quantity, limitPrice, tif } = current;
  const update = (patch: Partial<ListedOrderFormValues>) =>
    setForm((prev) => ({ ...(prev.contract === contractKey ? prev : blankForm(contractKey)), ...patch }));
  const setAction = (next: OrderAction) => update({ action: next });
  const setQuantity = (next: string) => update({ quantity: next });
  const setLimitPrice = (next: string) => update({ limitPrice: next });
  const setTif = (next: OrderTif) => update({ tif: next });
  const [submitting, setSubmitting] = useState(false);
  const inFlightRef = useRef(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitOk, setSubmitOk] = useState<PlaceOrderFeedback | null>(null);
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
  const maxLossUnbounded =
    riskSnapshot?.input === riskInput && riskSnapshot?.state.summary.maxLossUnbounded === true;

  // Two-step gate, same machine as the other tickets: Review arms for the
  // exact POST body; any change (contract included, via conId) disarms.
  const submit = buildSubmit(values);
  const payload = "payload" in submit ? submit.payload : null;
  const payloadKey = payload == null ? null : canonicalPayloadKey({ url: PLACE_URL, body: payload });
  const confirm = useArmedConfirm(payloadKey);
  const armed = confirm.armed;
  const transmitArmed = !maxLossUnbounded || confirm.acknowledged;

  // Form submit (the Review button, or Enter in any field) only ever reviews.
  // Transmit is a separate button, so Enter can never send.
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (armed) return;
    setSubmitError(null);
    setSubmitOk(null);

    if (!riskInput || !riskPermitsSubmit) {
      setSubmitError("Portfolio risk is still resolving. Review again before submitting.");
      return;
    }
    if ("error" in submit) {
      setSubmitError(submit.error);
      return;
    }
    confirm.arm();
  };

  const handleTransmit = async () => {
    if (!armed || payload == null || "error" in submit) return;
    if (!riskPermitsSubmit || !transmitArmed || submitDisabled) return;
    // `submitting` is state and invisible to a second call in the same tick.
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setSubmitError(null);
    setSubmitOk(null);
    setSubmitting(true);
    try {
      const res = await fetch(PLACE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store",
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        throw new Error((body.error as string) ?? `Order failed (${res.status})`);
      }
      setSubmitOk(placeOrderFeedback(body, submit.successText));
      confirm.disarm();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Order failed");
    } finally {
      inFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const label = typeof submitLabel === "function" ? submitLabel(action) : submitLabel;

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

      <OrderQuoteTelemetry priceData={priceData ?? null} label={quoteLabel} density="tight" />
      {quoteProvenance && (
        <div className="order-quote-provenance">{quoteProvenance}</div>
      )}

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

      {armed && maxLossUnbounded && (
        <div className="ticket-unbounded" data-testid="ticket-unbounded-warning">
          <label className="ticket-unbounded-row">
            <input
              type="checkbox"
              data-testid="ticket-unbounded-ack"
              checked={confirm.acknowledged}
              onChange={(e) => confirm.setAcknowledged(e.target.checked)}
            />
            <span>MAX LOSS UNBOUNDED. Loss grows without limit. Acknowledge to enable transmit.</span>
          </label>
        </div>
      )}

      {armed ? (
        <div className="order-confirm-row">
          <button
            type="button"
            className="btn-secondary"
            data-testid="listed-order-back"
            onClick={confirm.disarm}
            disabled={submitting}
          >
            Back
          </button>
          <button
            type="button"
            className="futures-form-submit"
            data-testid="listed-order-transmit"
            onClick={handleTransmit}
            disabled={submitting || submitDisabled || !riskPermitsSubmit || !transmitArmed}
          >
            {submitting
              ? "Submitting…"
              : transmitArmed
                ? `Transmit ${label}`
                : "Transmit: awaiting acknowledgement"}
          </button>
        </div>
      ) : (
        <button
          type="submit"
          data-testid="listed-order-review"
          disabled={submitting || submitDisabled || !riskPermitsSubmit}
          className="futures-form-submit"
        >
          {`Review ${label}`}
        </button>
      )}

      <OrderErrorBanner error={submitError} />
      {submitOk && (
        <div
          className={`futures-form-success${submitOk.deduplicated ? " futures-form-success--dedup" : ""}`}
        >
          {submitOk.message}
        </div>
      )}
    </form>
  );
}
