// Plain anchor, no next/link — Next.js 16 prerenders /_not-found in a
// worker without a layout-router context, and Link triggers a useContext
// invariant during static generation.

export default function TickerNotFound() {
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "60vh",
      fontFamily: "var(--font-mono)",
      color: "var(--text-secondary)",
      gap: "16px",
    }}>
      <span style={{ fontSize: "11px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--fault, #E85D6C)" }}>
        404 — Instrument Not Found
      </span>
      <span style={{ fontSize: "12px" }}>
        The requested ticker path is not a valid instrument identifier.
      </span>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- next/link triggers a useContext invariant during /_not-found prerender */}
      <a
        href="/dashboard"
        style={{
          fontSize: "11px",
          color: "var(--signal-core, #05AD98)",
          textDecoration: "none",
          borderBottom: "1px solid var(--signal-core, #05AD98)",
          paddingBottom: "1px",
        }}
      >
        Return to Dashboard
      </a>
    </div>
  );
}
