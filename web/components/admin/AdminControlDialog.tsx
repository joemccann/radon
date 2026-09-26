"use client";

import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDialogChrome } from "@/lib/useDialogChrome";
import styles from "./adminActionQueue.module.css";

/** Keep the original control owner mounted across open/close and confirmation. */
export default function AdminControlDialog({ open, onClose, title, id, children }: {
  open: boolean;
  onClose: () => void;
  title: string;
  id: string;
  children: ReactNode;
}) {
  const { portalTarget, panelRef } = useDialogChrome<HTMLDivElement>({ open, onClose });
  if (!portalTarget) return null;
  return createPortal(
    <div className={styles.tradingBackdrop} hidden={!open} data-testid={`${id}-dialog`} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div ref={panelRef} className={`${styles.tradingPanel} ${styles.controlPanel}`} data-controls={id} tabIndex={-1}>
        <header className={styles.controlPanelHeader}>
          <h2 id={`${id}-title`}>{title}</h2>
          <button type="button" className="admin-btn admin-btn-ghost" onClick={onClose} aria-label={`Close ${title.toLowerCase()}`}>Close</button>
        </header>
        {children}
      </div>
    </div>, portalTarget,
  );
}
