"use client";

interface SignalSummaryProps {
  confidence?: number;
  sourceDelay?: string;
  engine?: string;
  basis?: string;
}

export function SignalSummary({
  confidence = 0.84,
  sourceDelay = "132ms",
  engine = "Spectral",
  basis = "1m / 5d",
}: SignalSummaryProps) {
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
            Flow Module / 01
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Signal Summary
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
          Clear
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
          {confidence.toFixed(2)}
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
          Confidence
        </div>
      </div>

      <div className="flex flex-col" style={{ gap: 12 }}>
        {[
          { label: "source.delay", value: sourceDelay },
          { label: "engine", value: engine },
          { label: "basis", value: basis },
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
