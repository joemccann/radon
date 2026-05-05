"use client";

// Minimal global-error: pure server-style render, no hooks, no client
// imports beyond inline styles. Next.js 16 prerender of /_global-error
// crashes (TypeError: Cannot read properties of null reading useContext)
// when no explicit global-error.tsx exists and the auto-generated default
// tries to render under the isolated static-generation worker.
// Mirrors web/app/global-error.tsx — see CLAUDE.md "Production Build
// Constraint" for context.

type AppError = Error & { digest?: string };

export default function GlobalError({ error }: { error: AppError; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ background: "#0a0f14", color: "#94a3b8", fontFamily: "monospace", margin: 0 }}>
        <div style={{ padding: "48px 24px", textAlign: "center" }}>
          <p style={{ color: "#E85D6C", fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase" }}>
            Application Error
          </p>
          <p style={{ fontSize: 12 }}>radon.run could not render. Reload the page.</p>
          {error?.digest ? <p style={{ fontSize: 10 }}>Digest: {error.digest}</p> : null}
        </div>
      </body>
    </html>
  );
}
