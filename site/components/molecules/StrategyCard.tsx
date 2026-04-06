import { CommandChip } from "@/components/atoms/CommandChip";
import { EdgeTrace } from "@/components/atoms/EdgeTrace";
import { SignalPill } from "@/components/atoms/SignalPill";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import type { StrategyItem } from "@/lib/landing-content";

type LegacyStrategy = {
  name: string;
  edge: string;
  instruments: string;
  holdPeriod: string;
  winRate: string;
  riskType: string;
  command?: string;
  commands?: string[];
  state?: "clear" | "strong" | "warn" | "dislocated" | "emerging" | "muted";
  description?: string;
};

type NormalizedStrategy = {
  name: string;
  description: string;
  edge: string;
  instruments: string;
  holdPeriod: string;
  winRate: string;
  riskType: string;
  command: string;
  tone: "core" | "warn" | "violet" | "clear" | "strong" | "dislocated" | "emerging" | "muted";
};

const riskTone = {
  "Defined Risk": "core" as const,
  "Undefined Risk": "warn" as const,
  Overlay: "violet" as const,
};

function normalizeStrategy(strategy: StrategyItem | LegacyStrategy): NormalizedStrategy {
  if ("description" in strategy && typeof strategy.description === "string") {
    const nextStrategy = strategy as StrategyItem;
    return {
      name: nextStrategy.name,
      description: nextStrategy.description,
      edge: nextStrategy.edge,
      instruments: nextStrategy.instruments,
      holdPeriod: nextStrategy.holdPeriod,
      winRate: nextStrategy.winRate,
      riskType: nextStrategy.riskType,
      command: nextStrategy.command,
      tone: nextStrategy.tone ?? "core",
    };
  }

  const legacy = strategy as LegacyStrategy;
  const legacyRisk =
    legacy.riskType === "defined"
      ? "Defined Risk"
      : legacy.riskType === "undefined"
        ? "Undefined Risk"
        : legacy.riskType;

  return {
    name: legacy.name,
    description: legacy.description ?? legacy.edge,
    edge: legacy.edge,
    instruments: legacy.instruments,
    holdPeriod: legacy.holdPeriod,
    winRate: legacy.winRate,
    riskType: legacyRisk,
    command: legacy.command ?? legacy.commands?.join(" / ") ?? "command surface",
    tone: legacy.state ?? "core",
  };
}

export function StrategyCard({
  strategy,
}: {
  strategy: StrategyItem | LegacyStrategy;
}) {
  const normalized = normalizeStrategy(strategy);
  const tone = normalized.tone === "dislocated" ? "violet" : normalized.tone;

  return (
    <article className="relative flex h-full flex-col border border-grid bg-panel px-5 py-5">
      <EdgeTrace tone={tone} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <TelemetryLabel tone="core">Strategy Module</TelemetryLabel>
          <h3 className="mt-3 font-sans text-xl font-semibold text-primary">
            {normalized.name}
          </h3>
        </div>
        <SignalPill tone={riskTone[normalized.riskType as keyof typeof riskTone] ?? "neutral"}>
          {normalized.riskType}
        </SignalPill>
      </div>
      <p className="mt-3 text-sm font-medium leading-6 text-accent">{normalized.edge}</p>
      <p className="mt-2 text-sm leading-6 text-secondary">{normalized.description}</p>
      <dl className="mt-6 grid gap-4 border-t border-grid pt-5 sm:grid-cols-2">
        <div>
          <dt><TelemetryLabel>Instruments</TelemetryLabel></dt>
          <dd className="mt-2 text-sm leading-6 text-primary">{normalized.instruments}</dd>
        </div>
        <div>
          <dt><TelemetryLabel>Hold</TelemetryLabel></dt>
          <dd className="mt-2 font-mono text-[13px] uppercase tracking-[0.16em] text-secondary">
            {normalized.holdPeriod}
          </dd>
        </div>
        <div>
          <dt><TelemetryLabel>Win Rate</TelemetryLabel></dt>
          <dd className="mt-2 font-mono text-[13px] uppercase tracking-[0.16em] text-secondary">
            {normalized.winRate}
          </dd>
        </div>
      </dl>
      <div className="mt-6 flex items-center justify-between gap-4 border-t border-grid pt-5">
        <TelemetryLabel tone="muted">Command Surface</TelemetryLabel>
        <CommandChip command={normalized.command} />
      </div>
    </article>
  );
}
