"use client";

import ErrorToast from "@/components/ErrorToast";

import { userErrorMessage } from "@/lib/userError";
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
    <ErrorToast message={userErrorMessage(banner.message, "The broker connection is unavailable. Check connection status before trading.")} testId="ib-connection-banner" />
  );
}
