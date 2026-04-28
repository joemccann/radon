"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Columns3 } from "lucide-react";

export type ColumnsToggleEntry<K extends string> = {
  key: K;
  label: string;
  /** When true, disables the checkbox (column is non-toggleable). */
  alwaysOn?: boolean;
};

type Props<K extends string> = {
  columns: readonly ColumnsToggleEntry<K>[];
  visible: Record<K, boolean>;
  onToggle: (key: K) => void;
  onReset?: () => void;
};

/**
 * Compact "Columns" dropdown for data tables. Mirrors the brand kit:
 * 4px border-radius, hairline border, monospace numbers/labels in caps for
 * the trigger, sentence-case in the menu rows.
 *
 * Click outside the popover closes it.
 */
export function ColumnsToggle<K extends string>({ columns, visible, onToggle, onReset }: Props<K>) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const visibleCount = columns.filter((c) => visible[c.key]).length;

  return (
    <div ref={containerRef} className="columns-toggle">
      <button
        className="columns-toggle-btn"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Show or hide columns"
      >
        <Columns3 size={12} />
        <span className="columns-toggle-label">COLUMNS</span>
        <span className="columns-toggle-count">{visibleCount}/{columns.length}</span>
        <ChevronDown size={10} />
      </button>
      {open && (
        <div className="columns-toggle-menu" role="menu">
          {columns.map((col) => (
            <label
              key={col.key}
              className={`columns-toggle-item${col.alwaysOn ? " columns-toggle-item-locked" : ""}`}
            >
              <input
                type="checkbox"
                checked={Boolean(visible[col.key])}
                disabled={col.alwaysOn}
                onChange={() => onToggle(col.key)}
              />
              <span className="columns-toggle-item-label">{col.label}</span>
              {col.alwaysOn && <span className="columns-toggle-item-locked-hint">always</span>}
            </label>
          ))}
          {onReset && (
            <button className="columns-toggle-reset" onClick={onReset}>
              Reset to defaults
            </button>
          )}
        </div>
      )}
    </div>
  );
}
