"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, X } from "lucide-react";

interface FlexTokenStatus {
  days_remaining: number | null;
  expires_at: string;
  renewal_url: string;
  breadcrumb: string;
  should_warn: boolean;
  expired: boolean;
  active_threshold: number | null;
  token_masked: string;
}

/**
 * FlexTokenBanner — shows a warning banner when the IB Flex Web Service
 * token is approaching expiry (30, 14, 7, 1 days) or has expired.
 *
 * Renders nothing when should_warn is false.
 * Dismissible per session (stays dismissed until page reload).
 */
export default function FlexTokenBanner() {
  const [data, setData] = useState<FlexTokenStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Only check once per session
    fetch("/api/flex-token")
      .then((r) => r.json())
      .then((d: FlexTokenStatus) => setData(d))
      .catch(() => {}); // silently ignore if endpoint unavailable
  }, []);

  if (!data || !data.should_warn || dismissed) return null;

  const { days_remaining, expired, renewal_url, breadcrumb, token_masked } = data;

  const urgencyColor = expired
    ? "var(--negative, #E85D6C)"
    : days_remaining !== null && days_remaining <= 7
      ? "var(--negative, #E85D6C)"
      : "var(--warning, #F5A623)";

  const label = expired
    ? "EXPIRED"
    : days_remaining !== null && days_remaining <= 1
      ? "EXPIRES TOMORROW"
      : `EXPIRES IN ${days_remaining} DAYS`;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        padding: "8px 14px",
        background: expired
          ? "rgba(232,93,108,0.10)"
          : "rgba(245,166,35,0.08)",
        borderLeft: `3px solid ${urgencyColor}`,
        borderBottom: "1px solid var(--line-grid, #1e293b)",
        fontFamily: "var(--font-mono, 'IBM Plex Mono', monospace)",
        fontSize: "11px",
        letterSpacing: "0.04em",
      }}
    >
      <AlertTriangle size={14} color={urgencyColor} style={{ flexShrink: 0 }} />

      <span style={{ color: urgencyColor, fontWeight: 700 }}>
        IB FLEX TOKEN {label}
      </span>

      <span style={{ color: "var(--text-muted, #64748b)" }}>
        {token_masked}
      </span>

      <a
        href={renewal_url}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
          color: "var(--signal-core, #05AD98)",
          textDecoration: "none",
          fontWeight: 600,
        }}
      >
        Renew Token <ExternalLink size={11} />
      </a>

      <span
        style={{
          color: "var(--text-muted, #475569)",
          fontSize: "10px",
          marginLeft: "auto",
        }}
        title={breadcrumb}
      >
        {breadcrumb}
      </span>

      <button
        onClick={() => setDismissed(true)}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--text-muted, #64748b)",
          padding: "2px",
          flexShrink: 0,
        }}
        title="Dismiss until next session"
      >
        <X size={14} />
      </button>
    </div>
  );
}
