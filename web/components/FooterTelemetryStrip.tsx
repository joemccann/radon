"use client";

import { useEffect, useState } from "react";
import { useIBStatusContext, type IBDisplayStatus } from "@/lib/IBStatusContext";
import { useServiceHealth } from "@/lib/useServiceHealth";

type FlexTokenStatus = {
  days_remaining: number | null;
  expires_at: string;
  renewal_url: string;
  breadcrumb: string;
  should_warn: boolean;
  expired: boolean;
  active_threshold: number | null;
  token_masked: string;
};

type ChipTone = "ok" | "warn" | "dead" | "demo";

function ibChipFor(status: IBDisplayStatus): { text: string; cls: ChipTone } {
  switch (status) {
    case "connected":
      return { text: "Nominal", cls: "ok" };
    case "awaiting_2fa":
      return { text: "Awaiting 2FA", cls: "warn" };
    case "unhealthy":
      return { text: "Degraded", cls: "warn" };
    case "unreachable":
      return { text: "Unreachable", cls: "dead" };
    case "ib_offline":
      return { text: "Offline", cls: "dead" };
    case "relay_offline":
      return { text: "Relay offline", cls: "dead" };
    case "demo":
      return { text: "Sample data", cls: "demo" };
  }
}

function flexChipFor(flex: FlexTokenStatus | null): { text: string; cls: ChipTone } {
  if (!flex) return { text: "Unknown", cls: "warn" };
  if (flex.expired) return { text: "Expired", cls: "dead" };
  if (flex.days_remaining == null) return { text: "Active", cls: "ok" };
  const cls: ChipTone = flex.days_remaining <= 7 ? "dead" : flex.should_warn ? "warn" : "ok";
  return { text: `${flex.days_remaining}d remaining`, cls };
}

export function servicesChipFor(
  total: number | null,
  degraded: number,
  unavailable: boolean,
): { text: string; cls: ChipTone } {
  if (unavailable) return { text: "Unavailable", cls: "warn" };
  if (total == null || total <= 0) return { text: "Unknown", cls: "warn" };
  if (degraded === 0) return { text: `${total} of ${total} nominal`, cls: "ok" };
  return {
    text: `${total - degraded} of ${total} nominal · ${degraded} degraded`,
    cls: "warn",
  };
}

/**
 * FooterTelemetryStrip — replaces the stack of ConnectionBanner +
 * FlexTokenBanner + ServiceHealthBanner with a single instrument-readout
 * strip pinned to the bottom of the workspace. State persists at the
 * footer; transient events still fire via the toast system in WorkspaceShell.
 */
export default function FooterTelemetryStrip() {
  const { displayStatus } = useIBStatusContext();
  const { data: services, error: servicesError } = useServiceHealth();
  const [flex, setFlex] = useState<FlexTokenStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/flex-token", { signal: AbortSignal.timeout(10_000) })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: FlexTokenStatus | null) => {
        if (!cancelled && d) setFlex(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const ib = ibChipFor(displayStatus);
  const flexChip = flexChipFor(flex);
  const servicesChip = servicesChipFor(
    services?.summary?.total ?? null,
    services?.degraded_count ?? 0,
    servicesError != null,
  );

  return (
    <div className="footer-strip" role="status" aria-label="System telemetry">
      <Chip
        leadingDot
        label="IB Gateway"
        value={ib.text}
        tone={ib.cls}
      />
      <span className="footer-strip__sep" aria-hidden>/</span>
      <Chip
        label="Flex Token"
        value={flexChip.text}
        tone={flexChip.cls}
        title={flex?.breadcrumb}
      />
      <span className="footer-strip__sep" aria-hidden>/</span>
      <Chip
        label="Services"
        value={servicesChip.text}
        tone={servicesChip.cls}
      />
      <span className="footer-strip__spacer" />
      <span className="footer-strip__brand">Radon Terminal</span>
    </div>
  );
}

function Chip({
  label,
  value,
  tone,
  leadingDot,
  title,
}: {
  label: string;
  value: string;
  tone: ChipTone;
  leadingDot?: boolean;
  title?: string;
}) {
  return (
    <span
      className={`footer-strip__chip footer-strip__chip--${tone}`}
      data-tone={tone}
      title={title}
    >
      {leadingDot ? (
        <span
          className={`footer-strip__dot footer-strip__dot--${tone}`}
          aria-hidden
        />
      ) : null}
      <span className="footer-strip__k">{label}</span>
      <span className="footer-strip__v">{value}</span>
    </span>
  );
}
