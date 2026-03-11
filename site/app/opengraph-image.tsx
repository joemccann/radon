import { ImageResponse } from "next/og";

export const size = {
  width: 1200,
  height: 630,
};

export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          width: "100%",
          height: "100%",
          background: "#0a0f14",
          color: "#f5f7fa",
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            width: "100%",
            border: "1px solid #1e293b",
            margin: "28px",
            padding: "40px",
            background: "#0f1519",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 22,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: "#94a3b8",
            }}
          >
            <span>Radon Terminal</span>
            <span style={{ color: "#05AD98" }}>Protocol Nominal</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "22px", maxWidth: "860px" }}>
            <div
              style={{
                display: "flex",
                width: "190px",
                padding: "10px 14px",
                border: "1px solid #05AD98",
                color: "#05AD98",
                fontSize: 18,
                letterSpacing: "0.2em",
                textTransform: "uppercase",
              }}
            >
              Institutional Terminal
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <span style={{ fontSize: 72, fontWeight: 700, lineHeight: 1.02 }}>
                Strategies, execution, and state reconstruction in one instrument.
              </span>
              <span style={{ fontSize: 28, lineHeight: 1.4, color: "#cbd5e1" }}>
                Strategy discovery, execution discipline, and explainable market-structure telemetry.
              </span>
            </div>
          </div>
          <div style={{ display: "flex", gap: "18px", flexWrap: "wrap" }}>
            {[
              "Dark Pool + OI",
              "Execution Rail",
              "Kelly Discipline",
              "Regime Context",
            ].map((label) => (
              <div
                key={label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  border: "1px solid #1e293b",
                  background: "#151c22",
                  padding: "14px 18px",
                  fontSize: 20,
                  color: "#e2e8f0",
                }}
              >
                {label}
              </div>
            ))}
          </div>
        </div>
      </div>
    ),
    size,
  );
}
