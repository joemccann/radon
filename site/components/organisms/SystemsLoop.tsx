"use client";

import { useMemo, useState } from "react";
import {
  SYSTEM_LOOP_CASES,
  type ViewKey,
} from "@/lib/systems-loop";

export function SystemsLoop() {
  const [active, setActive] = useState<ViewKey>("flow");
  const selected = useMemo(
    () => SYSTEM_LOOP_CASES.find((entry) => entry.key === active) ?? SYSTEM_LOOP_CASES[0],
    [active],
  );

  return (
    <div className="overflow-hidden rounded-[10px] border border-grid bg-panel">
      <div className="grid md:grid-cols-2">
        <section className="border-b border-grid p-6 md:border-b-0 md:border-r md:p-8" aria-labelledby="view-chamber-title">
          <p className="text-[12px] font-medium text-signal-deep">1. Construct the view</p>
          <h3 id="view-chamber-title" className="mt-2 font-sans text-[1.45rem] font-[550] tracking-[-0.03em] text-primary">
            What is the market doing that price has not shown?
          </h3>
          <div role="tablist" aria-label="View sources" className="mt-6 flex flex-wrap gap-2">
            {SYSTEM_LOOP_CASES.map((entry) => {
              const selectedTab = entry.key === active;
              return (
                <button
                  key={entry.key}
                  type="button"
                  role="tab"
                  aria-selected={selectedTab}
                  className={`min-h-11 rounded-[8px] px-3.5 text-[14px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 ${
                    selectedTab
                      ? "bg-[color-mix(in_srgb,var(--color-accent)_12%,transparent)] text-signal-deep"
                      : "bg-panel-raised text-secondary hover:text-primary"
                  }`}
                  onClick={() => setActive(entry.key)}
                >
                  {entry.label}
                </button>
              );
            })}
          </div>
          <p className="mt-6 max-w-[42ch] text-[16px] leading-7 text-secondary">{selected.reading}</p>
          <p className="mt-3 font-mono text-[12px] text-muted">{selected.evidence}</p>
        </section>
        <section className="bg-panel-raised p-6 md:p-8" aria-labelledby="expression-chamber-title">
          <p className="text-[12px] font-medium text-signal-deep">2. Express the view</p>
          <h3 id="expression-chamber-title" className="mt-2 font-sans text-[1.45rem] font-[550] tracking-[-0.03em] text-primary">
            What is the cheapest convex way to own it?
          </h3>
          <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-5">
            <div>
              <dt className="text-[12px] text-muted">Name</dt>
              <dd className="mt-1 font-sans text-[22px] font-medium tracking-[-0.04em] text-primary">{selected.ticker}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Vehicle</dt>
              <dd className="mt-1 text-[16px] text-primary">{selected.vehicle}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Structure</dt>
              <dd className="mt-1 text-[16px] text-primary">{selected.structure}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Expiry</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.expiry}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Strikes</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.strikes}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Debit</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.debit}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Max loss</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.maxLoss}</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted">Convexity</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.convexity}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-[12px] text-muted">Kelly size</dt>
              <dd className="mt-1 font-mono text-[16px] text-primary">{selected.size}</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  );
}
