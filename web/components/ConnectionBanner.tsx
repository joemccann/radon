"use client";

import { useEffect, useRef, useState } from "react";
import { WifiOff, Wifi } from "lucide-react";

type ConnectionBannerProps = {
  ibConnected: boolean;
  wsConnected: boolean;
};

export default function ConnectionBanner({ ibConnected, wsConnected }: ConnectionBannerProps) {
  const [visible, setVisible] = useState(false);
  const [reconnected, setReconnected] = useState(false);
  const wasDisconnectedRef = useRef(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isDown = !ibConnected || !wsConnected;

  useEffect(() => {
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }

    if (isDown) {
      wasDisconnectedRef.current = true;
      setReconnected(false);
      setVisible(true);
    } else if (wasDisconnectedRef.current) {
      // Just reconnected — show green flash
      setReconnected(true);
      setVisible(true);
      hideTimerRef.current = setTimeout(() => {
        setVisible(false);
        setReconnected(false);
        wasDisconnectedRef.current = false;
      }, 2500);
    }

    return () => {
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    };
  }, [isDown]);

  if (!visible) return null;

  const message = reconnected
    ? "IB Gateway reconnected"
    : !wsConnected
      ? "Realtime server unreachable — prices and portfolio data are stale"
      : "IB Gateway disconnected — prices and portfolio data are stale";

  return (
    <div className={`connection-banner ${reconnected ? "connected" : ""}`} role="alert">
      {reconnected ? <Wifi size={14} /> : <WifiOff size={14} />}
      <span>{message}</span>
    </div>
  );
}
