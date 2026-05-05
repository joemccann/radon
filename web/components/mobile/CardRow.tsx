"use client";

import type { ReactNode } from "react";

type CardRowProps = {
  label?: ReactNode;
  value: ReactNode;
  align?: "default" | "compact";
  tone?: "default" | "positive" | "negative" | "warning" | "muted";
  testId?: string;
};

const toneClass: Record<NonNullable<CardRowProps["tone"]>, string> = {
  default: "",
  positive: "mobile-card-row__value--positive",
  negative: "mobile-card-row__value--negative",
  warning: "mobile-card-row__value--warning",
  muted: "mobile-card-row__value--muted",
};

export function CardRow({ label, value, align = "default", tone = "default", testId }: CardRowProps) {
  return (
    <div className={`mobile-card-row mobile-card-row--${align}`} data-testid={testId}>
      {label !== undefined ? <span className="mobile-card-row__label">{label}</span> : null}
      <span className={["mobile-card-row__value", toneClass[tone]].filter(Boolean).join(" ")}>{value}</span>
    </div>
  );
}

export default CardRow;
