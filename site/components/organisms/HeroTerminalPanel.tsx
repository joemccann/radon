"use client";

import { useCallback, useState } from "react";
import { CommandChip } from "@/components/atoms/CommandChip";
import { MonoMetric } from "@/components/atoms/MonoMetric";
import { SignalPill } from "@/components/atoms/SignalPill";
import { StatusDot } from "@/components/atoms/StatusDot";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { SourceRail } from "@/components/molecules/SourceRail";
import { TerminalNavItem } from "@/components/molecules/TerminalNavItem";
import { moduleContents, type ModuleTab } from "@/lib/landing-content";

const tabs: Array<{ id: ModuleTab; label: string }> = [
  { id: "flow", label: "Flow" },
  { id: "performance", label: "Performance" },
  { id: "structure", label: "Structure" },
  { id: "execution", label: "Execution" },
];

export function HeroTerminalPanel() {
  const [activeTab, setActiveTab] = useState<ModuleTab>("flow");
  const [animKey, setAnimKey] = useState(0);

  const handleTabChange = useCallback((tab: ModuleTab) => {
    setActiveTab(tab);
    setAnimKey((k) => k + 1);
  }, []);

  const content = moduleContents[activeTab];

  return (
    <div className="scan-line relative border border-grid bg-panel">
      <div className="absolute inset-0 projection-lines opacity-35" />
      <div className="relative z-20 border-b border-grid px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <TelemetryLabel tone="core">Radon Terminal</TelemetryLabel>
            <h2 className="mt-3 font-sans text-2xl font-semibold text-primary">
              Strategy state and execution path in one shell.
            </h2>
          </div>
          <div className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.16em] text-secondary">
            <StatusDot tone="strong" />
            Signal Chain Nominal
          </div>
        </div>
      </div>
      <div className="relative z-20 grid border-b border-grid lg:grid-cols-[160px_minmax(0,1fr)]">
        <nav className="bg-canvas px-4 py-5 lg:border-r lg:border-grid">
          <TelemetryLabel>Modules</TelemetryLabel>
          <div className="mt-4 space-y-1" role="tablist" aria-label="Terminal modules">
            {tabs.map((tab) => (
              <TerminalNavItem
                key={tab.id}
                label={tab.label}
                active={activeTab === tab.id}
                onClick={() => handleTabChange(tab.id)}
                role="tab"
                ariaSelected={activeTab === tab.id}
                id={`tab-${tab.id}`}
              />
            ))}
          </div>
        </nav>
        <div className="bg-panel px-4 py-5 overflow-hidden" role="tabpanel" aria-labelledby={`tab-${activeTab}`}>
          <div key={animKey} className="module-content-enter">
            <div className="grid gap-4 sm:grid-cols-2">
              {content.metrics.map((metric, index) => (
                <MonoMetric
                  key={metric.label}
                  label={metric.label}
                  value={metric.value}
                  detail={metric.detail}
                  tone={index % 2 === 0 ? "core" : "primary"}
                />
              ))}
            </div>
            <div className="mt-4 border border-grid bg-canvas">
              <div className="flex items-center justify-between border-b border-grid px-4 py-3">
                <TelemetryLabel tone="muted">{content.commandLabel}</TelemetryLabel>
                <SignalPill tone="strong">{content.commandPill}</SignalPill>
              </div>
              <div className="divide-y divide-grid">
                {content.commands.map((item) => (
                  <div
                    key={item.label}
                    className="flex flex-col gap-3 px-4 py-4 md:flex-row md:items-center md:justify-between"
                  >
                    <div>
                      <p className="font-sans text-sm font-medium text-primary">{item.label}</p>
                      <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.16em] text-secondary">
                        {item.state}
                      </p>
                    </div>
                    <CommandChip command={item.command} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="relative z-20 px-5 py-4">
        <SourceRail
          items={[
            { label: "IB Gateway", value: "Connected", tone: "strong" },
            { label: "Methodology", value: "Exposed", tone: "core" },
            { label: "Execution", value: "Operator Led", tone: "core" },
            { label: "Recency", value: "Session Scoped", tone: "warn" },
          ]}
        />
      </div>
    </div>
  );
}
