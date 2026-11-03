"use client";

import { useCallback, useRef, useState } from "react";
import { ChevronDown, Columns3 } from "lucide-react";
import { useDismissablePopover } from "@/lib/useDismissablePopover";
import { usePointerPresence } from "@/lib/usePointerPresence";

export type ColumnsToggleEntry<K extends string> = {
  key: K;
  label: string;
  /** When true, disables the checkbox (column is non-toggleable). */
  alwaysOn?: boolean;
  /** Optional section heading; grouped entries get per-group All / None. */
  group?: string;
};

type Props<K extends string> = {
  columns: readonly ColumnsToggleEntry<K>[];
  visible: Record<K, boolean>;
  onToggle: (key: K) => void;
  onReset?: () => void;
  /**
   * Anchor the menu with `position: fixed` under the trigger, for toolbars
   * that scroll horizontally (overflow would otherwise clip the dropdown).
   */
  floating?: boolean;
};

/**
 * Compact "Columns" dropdown for data tables. Mirrors the brand kit:
 * 4px border-radius, hairline border, monospace numbers/labels in caps for
 * the trigger, sentence-case in the menu rows.
 *
 * Click outside the popover closes it.
 */
export function ColumnsToggle<K extends string>({ columns, visible, onToggle, onReset, floating = false }: Props<K>) {
  const { open, mounted, setOpen, props: fade } = usePointerPresence("menu");
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const toggleOpen = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (!open && floating && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setAnchor({ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) });
    }
    setOpen(!open, event.detail > 0);
    if (open) buttonRef.current?.focus();
  };

  const close = useCallback((event?: MouseEvent | KeyboardEvent) => {
    setOpen(false, event?.type === "mousedown");
    if (event?.type === "keydown") buttonRef.current?.focus();
  }, [setOpen]);
  useDismissablePopover(containerRef, close, open);

  const visibleCount = columns.filter((c) => visible[c.key]).length;

  const renderItem = (col: ColumnsToggleEntry<K>) => (
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
  );

  const groups: string[] = [];
  for (const col of columns) {
    if (col.group && !groups.includes(col.group)) groups.push(col.group);
  }

  const setGroup = (group: string, on: boolean) => {
    for (const col of columns) {
      if (col.group === group && !col.alwaysOn && Boolean(visible[col.key]) !== on) onToggle(col.key);
    }
  };

  return (
    <div ref={containerRef} className="columns-toggle">
      <button
        ref={buttonRef}
        className="columns-toggle-btn"
        onClick={toggleOpen}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Show or hide columns"
      >
        <Columns3 size={12} />
        <span className="columns-toggle-label">COLUMNS</span>
        <span className="columns-toggle-count">{visibleCount}/{columns.length}</span>
        <ChevronDown size={10} />
      </button>
      {mounted && (
        <div
          {...fade}
          className={`pointer-fade columns-toggle-menu${groups.length ? " columns-toggle-menu--grouped" : ""}`}
          role="menu"
          style={{ ...fade.style, ...(floating && anchor ? { position: "fixed", top: anchor.top, right: anchor.right } as const : {}) }}
        >
          {columns.filter((col) => !col.group).map(renderItem)}
          {groups.map((group) => {
            const members = columns.filter((col) => col.group === group);
            const allOn = members.every((col) => visible[col.key]);
            const allOff = members.every((col) => col.alwaysOn || !visible[col.key]);
            return (
              <div key={group} className="columns-toggle-group" role="group" aria-label={group}>
                <div className="columns-toggle-group-head">
                  <span className="columns-toggle-group-name">{group}</span>
                  <button
                    type="button"
                    className="columns-toggle-group-action"
                    onClick={() => setGroup(group, true)}
                    disabled={allOn}
                    aria-label={`Show all ${group}`}
                  >
                    All
                  </button>
                  <button
                    type="button"
                    className="columns-toggle-group-action"
                    onClick={() => setGroup(group, false)}
                    disabled={allOff}
                    aria-label={`Hide all ${group}`}
                  >
                    None
                  </button>
                </div>
                {members.map(renderItem)}
              </div>
            );
          })}
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
