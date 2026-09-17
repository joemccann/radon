"use client";

import { userErrorMessage } from "@/lib/userError";
import { AlertTriangle } from "lucide-react";
import { getConnectionBannerState } from "@/lib/ibConnectionAlert";

type ConnectionBannerProps = {
  ibConnected: boolean;
  wsConnected: boolean;
  ibIssue: string | null;
  ibStatusMessage: string | null;
};

export default function ConnectionBanner({
  ibConnected,
  wsConnected,
  ibIssue,
  ibStatusMessage,
}: ConnectionBannerProps) {
  const banner = getConnectionBannerState({
    reconnected: false,
    wsConnected,
    ibConnected,
    ibIssue,
    ibStatusMessage,
  });

  if (!banner) return null;

  return (
    <div
      className="connection-banner"
      role="alert"
      data-testid="ib-connection-banner"
    >
      <AlertTriangle size={14} />
      <span>{userErrorMessage(banner.message, 'The broker connection is unavailable. Check connection status before trading.')}</span>
    </div>
  );
}
