"use client";

interface EnergyDistributionProps {
  data?: number[];
  engine?: string;
}

const DEFAULT_DATA = [
  12, 18, 28, 45, 62, 75, 85, 70, 62, 48, 35, 28, 22, 18, 28, 38, 52, 68, 78,
  82, 72, 58, 42, 32, 25, 20, 18, 15, 12, 10,
];

export function EnergyDistribution({
  data = DEFAULT_DATA,
  engine = "Spectral",
}: EnergyDistributionProps) {
  const maxValue = Math.max(...data);

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
            Spectral Engine / Detail
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Energy Distribution
          </h3>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--signal-core)",
          }}
        >
          {engine}
        </span>
      </div>

      <div
        className="flex items-end"
        style={{ height: 160, gap: 2, marginBottom: 16 }}
      >
        {data.map((value, index) => {
          const ratio = value / maxValue;
          return (
            <div
              key={index}
              style={{
                flex: 1,
                height: `${ratio * 100}%`,
                background: ratio > 0.7 ? "var(--signal-core)" : "var(--signal-deep)",
                opacity: 0.5 + ratio * 0.5,
                borderRadius: "2px 2px 0 0",
                transition: "opacity 150ms ease-in-out",
              }}
            />
          );
        })}
      </div>

      <p
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "var(--text-muted)",
        }}
      >
        Spectral decomposition bars. Use for decomposition, load states, and
        engine drift-down summaries.
      </p>
    </div>
  );
}
