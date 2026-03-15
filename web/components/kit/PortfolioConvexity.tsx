"use client";

interface PortfolioConvexityProps {
  deviation?: string;
  netGamma?: string;
  netVega?: string;
  signalLayer?: string;
}

export function PortfolioConvexity({
  deviation = "+2.73\u03C3",
  netGamma = "+0.61",
  netVega = "-1.24",
  signalLayer = "Radon Core",
}: PortfolioConvexityProps) {
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
            Exposure Module / 03
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Portfolio Convexity
          </h3>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--dislocation)",
          }}
        >
          Dislocated
        </span>
      </div>

      <div style={{ marginBottom: 24 }}>
        <div
          style={{
            fontSize: 32,
            fontWeight: 500,
            color: "var(--text-primary)",
            lineHeight: 1.05,
            marginBottom: 4,
          }}
        >
          {deviation}
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
          Local baseline deviation
        </div>
      </div>

      <div className="flex flex-col" style={{ gap: 12 }}>
        {[
          { label: "Net Gamma", value: netGamma },
          { label: "Net Vega", value: netVega },
          { label: "Signal Layer", value: signalLayer },
        ].map((row) => (
          <div key={row.label} className="flex justify-between items-center">
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--text-secondary)",
              }}
            >
              {row.label}
            </span>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--text-primary)",
              }}
            >
              {row.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
