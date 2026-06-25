"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import InfoTooltip from "./InfoTooltip";
import type { SortDirection } from "@/lib/useSort";

/* ─── Sortable header cell ─────────────────────────────── */

function shouldIgnoreSortEvent(target: EventTarget | null, currentTarget: EventTarget | null): boolean {
  if (!(target instanceof Element) || !(currentTarget instanceof Element) || target === currentTarget) {
    return false;
  }
  return Boolean(target.closest("button,a,input,select,textarea,[role='button'],[data-sort-ignore='true']"));
}

export default function SortTh<K extends string>({
  label,
  sortKey,
  activeKey,
  direction,
  onToggle,
  className,
  helpText,
  helpAriaLabel,
  helpTriggerTestId,
  helpContentTestId,
}: {
  label: ReactNode;
  sortKey: K;
  activeKey: K | null;
  direction: SortDirection;
  onToggle: (key: K) => void;
  className?: string;
  helpText?: string;
  helpAriaLabel?: string;
  helpTriggerTestId?: string;
  helpContentTestId?: string;
}) {
  const active = activeKey === sortKey;
  const ariaSort = active ? (direction === "asc" ? "ascending" : "descending") : undefined;
  const handleClick = (event: MouseEvent<HTMLTableCellElement>) => {
    if (shouldIgnoreSortEvent(event.target, event.currentTarget)) return;
    onToggle(sortKey);
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLTableCellElement>) => {
    if (shouldIgnoreSortEvent(event.target, event.currentTarget)) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onToggle(sortKey);
  };
  return (
    <th
      className={`sortable-th ${className ?? ""} ${active ? "sort-active" : ""}`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="columnheader"
      aria-sort={ariaSort}
    >
      <span className="sort-label">
        <span>{label}</span>
        {helpText && (
          <InfoTooltip
            text={helpText}
            ariaLabel={helpAriaLabel}
            triggerTestId={helpTriggerTestId}
            contentTestId={helpContentTestId}
          />
        )}
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
