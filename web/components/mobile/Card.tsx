"use client";

import type { ReactNode, MouseEvent, KeyboardEvent } from "react";

type CardProps = {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
  ariaLabel?: string;
  testId?: string;
  tone?: "default" | "positive" | "negative" | "warning";
};

const toneClass: Record<NonNullable<CardProps["tone"]>, string> = {
  default: "",
  positive: "mobile-card--positive",
  negative: "mobile-card--negative",
  warning: "mobile-card--warning",
};

export function Card({ children, onClick, className, ariaLabel, testId, tone = "default" }: CardProps) {
  const interactive = Boolean(onClick);
  const classes = [
    "mobile-card",
    interactive ? "mobile-card--interactive" : "",
    toneClass[tone],
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  if (!interactive) {
    return (
      <div className={classes} aria-label={ariaLabel} data-testid={testId}>
        {children}
      </div>
    );
  }

  const handleKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onClick?.();
    }
  };

  const handleClick = (_event: MouseEvent<HTMLDivElement>) => {
    onClick?.();
  };

  return (
    <div
      className={classes}
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKey}
      aria-label={ariaLabel}
      data-testid={testId}
    >
      {children}
    </div>
  );
}

export default Card;
