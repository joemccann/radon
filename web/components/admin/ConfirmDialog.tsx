"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialogChrome } from "@/lib/useDialogChrome";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** Units that will ALSO stop (cascade). Rendered as an enumerated warning. */
  affectedUnits?: string[];
  /** When set, gates Confirm behind typing this exact string (e.g. the unit
   *  name) and moves initial focus to the input instead of Confirm. */
  requireTyped?: string;
};

const EXIT_MS = 120;
const EXIT_REDUCED_MS = 80;

/**
 * Minimal confirmation dialog tailored for operator actions. Adopts the shared
 * useDialogChrome contract (escape + scroll lock + focus trap + focus restore)
 * so the cross-cutting behaviour lives in one place; the bespoke visuals and
 * the confirm-button-as-primary-affordance focus default stay local.
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = false,
  pending = false,
  onConfirm,
  onCancel,
  affectedUnits,
  requireTyped,
}: ConfirmDialogProps) {
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const typeInputRef = useRef<HTMLInputElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const [typed, setTyped] = useState("");
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [mounted, setMounted] = useState(open);
  const [exiting, setExiting] = useState(false);
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { portalTarget, panelRef } = useDialogChrome<HTMLDivElement>({
    open,
    onClose: onCancel,
  });

  // Enter/exit paint: keep the portal mounted ~120ms after open becomes false
  // so opacity + translateY can settle (shared Modal recipe).
  useEffect(() => {
    if (open) {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
      setMounted(true);
      setExiting(false);
      return;
    }
    if (!mounted) return;
    setExiting(true);
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ms = reduced ? EXIT_REDUCED_MS : EXIT_MS;
    exitTimerRef.current = setTimeout(() => {
      setMounted(false);
      setExiting(false);
      exitTimerRef.current = null;
    }, ms);
    return () => {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [open, mounted]);

  // Reset the typed-confirm field each time the dialog opens.
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  // Clear the "Copied" feedback timer on unmount.
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const copyToken = async () => {
    if (!requireTyped) return;
    await navigator.clipboard?.writeText(requireTyped);
    setCopied(true);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
  };

  // Focus discipline: a type-to-confirm gate focuses the input; a destructive
  // dialog focuses Cancel (never default focus to a danger button); otherwise
  // the primary Confirm. (Best-practice: don't let Enter fire a destructive op.)
  useEffect(() => {
    if (!open) return;
    if (requireTyped) typeInputRef.current?.focus();
    else if (destructive) cancelBtnRef.current?.focus();
    else confirmBtnRef.current?.focus();
  }, [open, requireTyped, destructive]);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (exiting || pending) return;
      if (e.target === e.currentTarget) onCancel();
    },
    [exiting, pending, onCancel],
  );

  if (!mounted || !portalTarget) return null;

  const typedOk = !requireTyped || typed.trim() === requireTyped;
  const confirmDisabled = pending || !typedOk || exiting;

  return createPortal(
    <div
      className={`admin-confirm-backdrop${exiting ? " admin-confirm-backdrop--exiting" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="admin-confirm"
      onClick={handleBackdropClick}
    >
      <div className="admin-confirm-panel" ref={panelRef} tabIndex={-1}>
        <h2 className="admin-confirm-title">{title}</h2>
        <p className="admin-confirm-body">{body}</p>

        {affectedUnits && affectedUnits.length > 0 && (
          <div className="admin-confirm-cascade" data-testid="admin-confirm-cascade">
            <span className="admin-confirm-cascade-label">Will also stop</span>
            <ul className="admin-confirm-cascade-list">
              {affectedUnits.map((u) => (
                <li key={u}>{u}</li>
              ))}
            </ul>
            <p className="admin-confirm-cascade-warn">
              These will NOT auto-restart. Use Restart All Services to recover the stack.
            </p>
          </div>
        )}

        {requireTyped && (
          <label className="admin-confirm-typed">
            <span>
              Type <code>{requireTyped}</code>
              <button
                type="button"
                className="admin-confirm-copy"
                onClick={copyToken}
                aria-label={`Copy ${requireTyped} to clipboard`}
                data-testid="admin-confirm-copy"
              >
                {copied ? "Copied" : "Copy"}
              </button>{" "}
              to confirm
            </span>
            <input
              ref={typeInputRef}
              type="text"
              className="admin-confirm-typed-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              data-testid="admin-confirm-typed-input"
            />
          </label>
        )}

        <div className="admin-confirm-actions">
          <button
            ref={cancelBtnRef}
            type="button"
            className="admin-btn admin-btn-ghost"
            onClick={onCancel}
            disabled={pending || exiting}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            className={`admin-btn ${destructive ? "admin-btn-danger" : "admin-btn-primary"}`}
            onClick={onConfirm}
            disabled={confirmDisabled}
            data-testid="admin-confirm-action"
          >
            {pending ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    portalTarget,
  );
}
