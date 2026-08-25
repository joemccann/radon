"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  buildQuoteTelemetryModel,
  type QuoteFallback,
  type QuoteTelemetryFieldKey,
  type QuoteTelemetryModel,
} from "@/lib/quoteTelemetry";

type QuoteTelemetryVariant = "bar" | "compact";

/**
 * Spacing/type-scale only. `tight` exists so the same nine fields fit a narrow
 * inline ticket or a mobile bottom sheet; it never changes WHICH fields render.
 */
export type QuoteTelemetryDensity = "default" | "tight";

const BAR_FIELDS: QuoteTelemetryFieldKey[] = ["bid", "mid", "ask", "spread", "last", "volume", "high", "low", "day"];
const COMPACT_FIELDS: QuoteTelemetryFieldKey[] = ["bid", "mid", "ask", "spread"];

const VARIANT_CLASSES = {
  bar: {
    container: "price-bar",
    empty: "price-bar price-bar-empty",
    row: "price-bar-item",
    label: "price-bar-label",
    value: "price-bar-value",
    emptyText: "No real-time data",
  },
  compact: {
    container: "modify-market-data",
    empty: "modify-market-warning",
    row: "modify-market-row",
    label: "modify-market-label",
    value: "modify-market-value",
    emptyText: "No real-time market data available",
  },
} as const;

function QuoteTelemetryPanel({
  model,
  label,
  fields,
  variant,
  density = "default",
}: {
  model: QuoteTelemetryModel | null;
  label?: string;
  fields: QuoteTelemetryFieldKey[];
  variant: QuoteTelemetryVariant;
  density?: QuoteTelemetryDensity;
}) {
  const classes = VARIANT_CLASSES[variant];
  if (!model) {
    return <div className={classes.empty}>{classes.emptyText}</div>;
  }

  const containerClass = density === "tight"
    ? `${classes.container} ${classes.container}--tight`
    : classes.container;

  return (
    <div className={containerClass}>
      {variant === "bar" && label && (
        <div className={classes.row} style={{ gridColumn: "1 / -1" }}>
          <span className={classes.label}>{label}</span>
        </div>
      )}
      {fields.map((fieldKey) => {
        const field = model[fieldKey];
        const toneClass = field.tone ? ` ${field.tone}` : "";
        return (
          <div key={fieldKey} className={classes.row}>
            <span className={classes.label}>{field.label}</span>
            <span className={`${classes.value}${toneClass}`}>
              {field.value}
              {field.trend === "up" && <ArrowUp size={10} className="price-trend-icon price-trend-up" />}
              {field.trend === "down" && <ArrowDown size={10} className="price-trend-icon price-trend-down" />}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export type OrderQuoteTelemetryProps = {
  /** The traded instrument's live quote. Omit (or pass null) on a combo and hand `model` instead. */
  priceData?: PriceData | null;
  /**
   * Escape hatch for surfaces that have no single PriceData (BAG / combo
   * tickets). Build it with `comboQuotePriceData()` + `buildQuoteTelemetryModel()`
   * so the spread math and the closed-market fallback stay in one place.
   * Takes precedence over `priceData`.
   */
  model?: QuoteTelemetryModel | null;
  /** e.g. "META 2026-08-28 $550 P". Rendered as a full-width row above the fields. */
  label?: string;
  /** Prior-session OHLV (UW stock-state) so a closed market shows CLOSE instead of an empty panel. */
  fallback?: QuoteFallback | null;
  /** Spacing and type scale only. `tight` for inline tickets and mobile sheets. */
  density?: QuoteTelemetryDensity;
};

/**
 * The one nine-field quote panel every order surface renders: BID MID ASK /
 * SPREAD LAST VOLUME / HIGH LOW DAY. Same model, same formatter, and the same
 * closed-market fallback the portfolio position drawer gets.
 */
export function OrderQuoteTelemetry({
  priceData = null,
  model,
  label,
  fallback,
  density = "default",
}: OrderQuoteTelemetryProps) {
  return (
    <QuoteTelemetryPanel
      model={model ?? buildQuoteTelemetryModel(priceData ?? null, fallback ?? null)}
      label={label}
      fields={BAR_FIELDS}
      variant="bar"
      density={density}
    />
  );
}

/** Thin alias. The ticker hero bar is the same nine-field panel. */
export const TickerQuoteTelemetry = OrderQuoteTelemetry;

/** Thin alias kept for existing order call sites. Prefer `OrderQuoteTelemetry`. */
export const InstrumentOrderQuoteTelemetry = OrderQuoteTelemetry;

export function ModifyOrderQuoteTelemetry({ priceData }: { priceData: PriceData | null }) {
  return (
    <QuoteTelemetryPanel
      model={buildQuoteTelemetryModel(priceData)}
      fields={COMPACT_FIELDS}
      variant="compact"
    />
  );
}
