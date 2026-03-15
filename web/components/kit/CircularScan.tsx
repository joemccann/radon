"use client";

interface CircularScanProps {
  status?: "Scanning" | "Standby" | "Complete";
}

export function CircularScan({ status = "Standby" }: CircularScanProps) {
  const isScanning = status === "Scanning";

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
            Discovery / Scan
          </p>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Circular Scan Motif
          </h3>
        </div>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: isScanning ? "var(--signal-core)" : "var(--text-muted)",
          }}
        >
          {status}
        </span>
      </div>

      <div className="flex items-center justify-center" style={{ height: 192 }}>
        <div style={{ position: "relative", width: 160, height: 160 }}>
          <svg viewBox="0 0 160 160" width="160" height="160">
            <circle cx="80" cy="80" r="75" fill="none" stroke="var(--border-dim)" strokeWidth="1" />
            <circle cx="80" cy="80" r="54" fill="none" stroke="var(--signal-deep)" strokeWidth="1" opacity="0.6" />
            <circle cx="80" cy="80" r="33" fill="none" stroke="var(--border-dim)" strokeWidth="1" />

            <line
              x1="80"
              y1="80"
              x2="80"
              y2="10"
              stroke="var(--signal-core)"
              strokeWidth="2"
              style={{
                transformOrigin: "80px 80px",
                animation: isScanning ? "radon-scan-rotate 4s linear infinite" : "none",
              }}
            />

            <circle cx="80" cy="80" r="4" fill="var(--text-primary)" />
          </svg>
        </div>
      </div>

      <style>{`
        @keyframes radon-scan-rotate {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
