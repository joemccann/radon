import type { ReactNode } from "react";
import { CopyAgentPrompt } from "@/components/molecules/CopyAgentPrompt";
import { getCapabilityPrompt } from "@/lib/agent-prompts";
import type { RegimeModel } from "@/lib/editorial-content";

// Each model carries a hand-built mini-figure (not a screenshot). Colors come
// from the `.fig-svg` classes in globals.css so they track the active theme.
const miniFigures: Record<string, ReactNode> = {
  CRI: (
    <svg
      viewBox="0 0 320 70"
      width="100%"
      role="img"
      aria-label="Crash Risk Index sparkline rising into an elevated band"
    >
      <line className="ref-line" x1="0" y1="28" x2="320" y2="28" />
      <text className="mini-label" x="0" y="20">
        elevated
      </text>
      <path
        className="spark-line ext"
        d="M0,56 L40,54 L80,52 L120,50 L160,46 L200,40 L240,30 L280,22 L320,16"
      />
      <text className="mini-label" x="320" y="66" textAnchor="end">
        CRI 0.71 · rising
      </text>
    </svg>
  ),
  GEX: (
    <svg
      viewBox="0 0 320 70"
      width="100%"
      role="img"
      aria-label="Gamma exposure by strike, a wall above and a magnet below spot"
    >
      <line className="ref-line" x1="160" y1="6" x2="160" y2="64" />
      <text className="mini-label" x="164" y="14">
        spot
      </text>
      <rect className="bar-strike neg" x="20" y="40" width="16" height="22" />
      <rect className="bar-strike neg" x="48" y="34" width="16" height="28" />
      <rect className="bar-strike" x="76" y="46" width="16" height="16" />
      <rect className="bar-strike" x="104" y="50" width="16" height="12" />
      <rect className="bar-strike wall" x="208" y="20" width="16" height="42" />
      <rect className="bar-strike" x="236" y="38" width="16" height="24" />
      <rect className="bar-strike" x="264" y="48" width="16" height="14" />
      <text className="mini-label" x="216" y="16" textAnchor="middle">
        wall
      </text>
    </svg>
  ),
  "VCG-R": (
    <svg
      viewBox="0 0 320 70"
      width="100%"
      role="img"
      aria-label="Two diverging lines marking a volatility-credit gap opening"
    >
      <path className="spark-line sec" d="M0,40 L60,38 L120,36 L180,34 L240,33 L320,32" />
      <path className="spark-line" d="M0,42 L60,44 L120,40 L180,30 L240,18 L320,10" />
      <text className="mini-label" x="320" y="66" textAnchor="end">
        gap 2.4σ · widening
      </text>
    </svg>
  ),
  GRG: (
    <svg
      viewBox="0 0 320 70"
      width="100%"
      role="img"
      aria-label="Sector rotation bars shifting positive"
    >
      <line className="ref-line" x1="0" y1="36" x2="320" y2="36" />
      <rect className="bar-strike" x="16" y="20" width="22" height="16" />
      <rect className="bar-strike" x="56" y="14" width="22" height="22" />
      <rect className="bar-strike" x="96" y="24" width="22" height="12" />
      <rect className="bar-strike neg" x="136" y="36" width="22" height="14" />
      <rect className="bar-strike neg" x="176" y="36" width="22" height="20" />
      <rect className="bar-strike" x="216" y="26" width="22" height="10" />
      <rect className="bar-strike" x="256" y="18" width="22" height="18" />
      <text className="mini-label" x="320" y="66" textAnchor="end">
        rotation into semis
      </text>
    </svg>
  ),
};

type RegimeModelCardProps = {
  model: RegimeModel;
};

const regimeCapability: Record<string, string> = {
  CRI: "cri",
  GEX: "gex",
};

export function RegimeModelCard({ model }: RegimeModelCardProps) {
  const capabilityId = regimeCapability[model.code];
  return (
    <div className="flex flex-col rounded-[4px] border border-grid bg-figure-bg p-[22px]">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <span className="font-mono text-[12px] font-semibold tracking-[0.06em] text-signal-deep">
          {model.code}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
          {model.reads}
        </span>
      </div>
      <h3 className="mb-[10px] mt-[6px] font-serif text-[1.24rem] font-medium text-primary">
        {model.name}
      </h3>
      <p className="mb-4 font-mono text-[11px] leading-[1.55] text-secondary">
        <b className="font-medium text-primary">Method.</b> {model.method}
      </p>
      <div className="mt-auto border-t border-hairline-soft pt-[14px] fig-svg">
        {miniFigures[model.code]}
      </div>
      {capabilityId ? (
        <div className="mt-4">
          <CopyAgentPrompt prompt={getCapabilityPrompt(capabilityId)} />
        </div>
      ) : null}
    </div>
  );
}
