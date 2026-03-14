"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  buildQuoteTelemetryModel,
  type QuoteTelemetryFieldKey,
  type QuoteTelemetryModel,
} from "@/lib/quoteTelemetry";

type QuoteTelemetryVariant = "bar" | "compact";

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
}: {
  model: QuoteTelemetryModel | null;
  label?: string;
  fields: QuoteTelemetryFieldKey[];
  variant: QuoteTelemetryVariant;
}) {
  const classes = VARIANT_CLASSES[variant];
  if (!model) {
    return <div className={classes.empty}>{classes.emptyText}</div>;
  }

  return (
    <div className={classes.container}>
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
              {field.trend === "up" && <ArrowUp size={10} className="pt-i ptu" />}
              {field.trend === "down" && <ArrowDown size={10} className="pt-i ptd" />}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function TickerQuoteTelemetry({
  priceData,
  label,
}: {
  priceData: PriceData | null;
  label?: string;
}) {
  return (
    <QuoteTelemetryPanel
      model={buildQuoteTelemetryModel(priceData)}
      label={label}
      fields={BAR_FIELDS}
      variant="bar"
    />
  );
}

export function InstrumentOrderQuoteTelemetry({
  priceData,
  label,
}: {
  priceData: PriceData | null;
  label?: string;
}) {
  return (
    <QuoteTelemetryPanel
      model={buildQuoteTelemetryModel(priceData)}
      label={label}
      fields={BAR_FIELDS}
      variant="bar"
    />
  );
}

export function ModifyOrderQuoteTelemetry({ priceData }: { priceData: PriceData | null }) {
  return (
    <QuoteTelemetryPanel
      model={buildQuoteTelemetryModel(priceData)}
      fields={COMPACT_FIELDS}
      variant="compact"
    />
  );
}
