"use client";

import type { ReactNode } from "react";

/**
 * ScannerInstrumentShell — rack-mount grammar for every scanner module.
 *
 * Signature of Radon Flow (scanner family): device-label eyebrow + instrument
 * title + control rail. Replaces the
 * generic "lucide icon + section title" SaaS table header so scanners read
 * as mountable instruments, not dashboard cards.
 *
 * Module IDs encode tab order on /scanner (FLOW 01 … GARCH 06).
 */

export type ScannerInstrumentShellProps = {
  /** Mono device label, e.g. "THETA / 03". */
  moduleId: string;
  /** Human panel name (sentence case). */
  title: string;
  /** Optional help control rendered after the title. */
  titleAccessory?: ReactNode;
  /** Right-side controls: pills, search, scan CTA. */
  controls?: ReactNode;
  /** Section body (table, empty, loading). */
  children: ReactNode;
  /** Optional calibration rail under the body. */
  rail?: { k: string; v: ReactNode }[];
  className?: string;
  testId: string;
};

export default function ScannerInstrumentShell({
  moduleId,
  title,
  titleAccessory,
  controls,
  children,
  rail,
  className,
  testId,
}: ScannerInstrumentShellProps) {
  const sectionClass = ["section", "instrument-section", className].filter(Boolean).join(" ");
  return (
    <section className={sectionClass} data-testid={testId}>
      <header className="section-header instrument-section__header">
        <div className="instrument-section__heading">
          <p className="panel-eyebrow">{moduleId}</p>
          <h2 className="panel-title instrument-section__title">
            {title}
            {titleAccessory}
          </h2>
        </div>
        {controls ? (
          <div className="instrument-section__controls">{controls}</div>
        ) : null}
      </header>
      {children}
      {rail && rail.length > 0 ? (
        <footer className="panel-meta-rail instrument-section__rail" aria-label="Module calibration">
          {rail.map((row) => (
            <div key={row.k} className="panel-meta-rail-item">
              <span className="k">{row.k}</span>
              <span className="v">{row.v}</span>
            </div>
          ))}
        </footer>
      ) : null}
    </section>
  );
}
