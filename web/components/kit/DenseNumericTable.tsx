"use client";

interface TableRow {
  symbol: string;
  signal: string;
  confidence: number;
  volGap: string;
  netGamma: string;
  state: "Baseline" | "Emerging" | "Clear" | "Strong" | "Dislocated" | "Extreme";
}

const STATE_COLOR_VARS: Record<string, string> = {
  Baseline: "var(--neutral)",
  Emerging: "var(--signal-deep)",
  Clear: "var(--signal-core)",
  Strong: "var(--signal-strong)",
  Dislocated: "var(--dislocation)",
  Extreme: "var(--extreme)",
};

const SAMPLE_DATA: TableRow[] = [
  { symbol: "SPX", signal: "Surface kink", confidence: 0.84, volGap: "+1.92", netGamma: "+0.61", state: "Clear" },
  { symbol: "QQQ", signal: "Flow cluster", confidence: 0.79, volGap: "+1.33", netGamma: "-0.12", state: "Emerging" },
  { symbol: "IWM", signal: "Regime transition", confidence: 0.68, volGap: "+2.08", netGamma: "-0.51", state: "Dislocated" },
];

const thStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  color: "var(--text-muted)",
  padding: "12px 16px",
  fontWeight: 500,
};

const tdStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 13,
  padding: "12px 16px",
  color: "var(--text-primary)",
};

interface DenseNumericTableProps {
  data?: TableRow[];
}

export function DenseNumericTable({ data = SAMPLE_DATA }: DenseNumericTableProps) {
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
            Measurement Array / Table
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Dense Numeric Table
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
          Array
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-dim)" }}>
              <th style={{ ...thStyle, textAlign: "left" }}>Symbol</th>
              <th style={{ ...thStyle, textAlign: "left" }}>Signal</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Confidence</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Vol Gap</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Net Gamma</th>
              <th style={{ ...thStyle, textAlign: "right" }}>State</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr
                key={i}
                style={{
                  borderBottom: "1px solid var(--border-dim)",
                  transition: "background 150ms ease-in-out",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = "var(--bg-hover)")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <td style={tdStyle}>{row.symbol}</td>
                <td style={{ ...tdStyle, color: "var(--text-secondary)" }}>
                  {row.signal}
                </td>
                <td style={{ ...tdStyle, textAlign: "right" }}>
                  {row.confidence.toFixed(2)}
                </td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{row.volGap}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{row.netGamma}</td>
                <td
                  style={{
                    ...tdStyle,
                    textAlign: "right",
                    color: STATE_COLOR_VARS[row.state] ?? "var(--neutral)",
                  }}
                >
                  {row.state}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
