"use client";

import { useEffect } from "react";

type AppError = Error & { digest?: string };

export default function ErrorBoundary({ error, reset }: { error: AppError; reset: () => void }) {
  useEffect(() => {
    console.error("[radon] route error boundary:", error);
  }, [error]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
        fontFamily: "var(--font-mono)",
        color: "var(--text-secondary)",
        gap: "16px",
        padding: "24px",
      }}
    >
      <span
        style={{
          fontSize: "11px",
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          color: "var(--fault, #E85D6C)",
        }}
      >
        Runtime Error
      </span>
      <span style={{ fontSize: "12px", textAlign: "center", maxWidth: "420px" }}>
        The workspace hit an unexpected failure. You can retry or return to the dashboard.
      </span>
      {error.digest ? (
        <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>Digest: {error.digest}</span>
      ) : null}
      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", justifyContent: "center" }}>
        <button
          type="button"
          onClick={() => reset()}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            padding: "8px 14px",
            borderRadius: "4px",
            border: "1px solid var(--border-dim, #1e293b)",
            background: "var(--bg-panel-raised, #151c22)",
            color: "var(--text-primary)",
            cursor: "pointer",
          }}
        >
          Retry
        </button>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- next/link triggers a useContext invariant during /_global-error prerender */}
        <a
          href="/dashboard"
          style={{
            fontSize: "11px",
            color: "var(--signal-core, #05AD98)",
            textDecoration: "none",
            borderBottom: "1px solid var(--signal-core, #05AD98)",
            paddingBottom: "1px",
            alignSelf: "center",
          }}
        >
          Return to Dashboard
        </a>
      </div>
    </div>
  );
}
