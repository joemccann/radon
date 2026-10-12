import { pageMetadata } from "@/lib/pageTitle";

// Plain anchor, no next/link — Next.js 16 prerenders /_not-found in a
// worker without a layout-router context, and Link triggers a useContext
// invariant during static generation. Pure JSX: no hooks, no globals.css.

export const metadata = pageMetadata("Instrument not found");

export default function TickerNotFound() {
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "60vh",
      color: "var(--text-secondary)",
      gap: "12px",
      padding: "0 16px",
      textAlign: "center",
    }}>
      <h1 style={{ margin: 0, fontSize: "22px", fontWeight: 600, color: "var(--text-primary)" }}>
        No instrument at this address
      </h1>
      <p style={{ margin: 0, fontSize: "14px" }}>
        Check the symbol, or search from the dashboard with ⌘K.
      </p>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- next/link triggers a useContext invariant during /_not-found prerender */}
      <a
        href="/dashboard"
        style={{
          fontSize: "14px",
          color: "var(--signal-core)",
          textDecoration: "none",
          borderBottom: "1px solid var(--signal-core)",
          paddingBottom: "1px",
        }}
      >
        Back to dashboard
      </a>
    </div>
  );
}
