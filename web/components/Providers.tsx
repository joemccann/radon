"use client";

import { IBStatusProvider } from "@/lib/IBStatusContext";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import { ThemeProvider } from "@/lib/ThemeContext";
import { RealtimeAuthProvider } from "@/lib/RealtimeAuthContext";
import { OfflineStatusProvider } from "@/lib/offline/OfflineStatusContext";
import ClerkThemeBridge from "@/components/ClerkThemeBridge";
import SignOutCachePurge from "@/components/SignOutCachePurge";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <ClerkThemeBridge>
        <SignOutCachePurge />
        <RealtimeAuthProvider>
          <OfflineStatusProvider>
            <IBStatusProvider>
              <OrderActionsProvider>
                <TickerDetailProvider>{children}</TickerDetailProvider>
              </OrderActionsProvider>
            </IBStatusProvider>
          </OfflineStatusProvider>
        </RealtimeAuthProvider>
      </ClerkThemeBridge>
    </ThemeProvider>
  );
}
