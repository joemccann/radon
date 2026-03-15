"use client";

const SIGNAL_STATES = [
  { label: "Baseline", bgVar: "--neutral", textDark: "#0a0f14", textLight: "#FFFFFF" },
  { label: "Emerging", bgVar: "--signal-deep", textDark: "#e2e8f0", textLight: "#FFFFFF" },
  { label: "Clear", bgVar: "--signal-core", textDark: "#0a0f14", textLight: "#FFFFFF" },
  { label: "Strong", bgVar: "--signal-strong", textDark: "#0a0f14", textLight: "#0a0f14" },
  { label: "Dislocated", bgVar: "--dislocation", textDark: "#e2e8f0", textLight: "#FFFFFF" },
  { label: "Extreme", bgVar: "--extreme", textDark: "#e2e8f0", textLight: "#FFFFFF" },
] as const;

interface MetricProps {
  value: string;
  label: string;
}

function Metric({ value, label }: MetricProps) {
  return (
    <div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 500,
          color: "var(--text-primary)",
          lineHeight: 1.05,
          marginBottom: 4,
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          color: "var(--text-muted)",
        }}
      >
        {label}
      </div>
    </div>
  );
}

export function SemanticStates() {
  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: "1px solid var(--border-dim)",
        borderRadius: 4,
        padding: 24,
      }}
    >
      <div className="flex justify-between items-start" style={{ marginBottom: 24 }}>
        <div>
          <p
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              color: "var(--text-muted)",
              marginBottom: 4,
            }}
          >
            Signal Badges
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Semantic States
          </h3>
        </div>
      </div>

      <div className="flex flex-wrap" style={{ gap: 8, marginBottom: 24 }}>
        {SIGNAL_STATES.map((state) => (
          <span
            key={state.label}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              padding: "6px 12px",
              borderRadius: 999,
              background: `var(${state.bgVar})`,
              color: state.textDark,
              fontWeight: 500,
            }}
          >
            {state.label}
          </span>
        ))}
      </div>

      <div
        className="grid grid-cols-2"
        style={{
          gap: 24,
          paddingTop: 16,
          borderTop: "1px solid var(--border-dim)",
        }}
      >
        <Metric value="0.84" label="Confidence" />
        <Metric value="+2.73σ" label="Deviation" />
        <Metric value="132ms" label="Latency" />
        <Metric value="99.2%" label="Uptime" />
      </div>
    </div>
  );
}
