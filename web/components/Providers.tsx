"use client";

import { IBStatusProvider } from "@/lib/IBStatusContext";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import { ThemeProvider } from "@/lib/ThemeContext";
import { RealtimeAuthProvider } from "@/lib/RealtimeAuthContext";
import { OfflineStatusProvider } from "@/lib/offline/OfflineStatusContext";
import { RouteRefreshProvider } from "@/lib/RouteRefreshContext";
import ClerkThemeBridge from "@/components/ClerkThemeBridge";
import SignOutCachePurge from "@/components/SignOutCachePurge";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <ClerkThemeBridge>
        <SignOutCachePurge />
        <RealtimeAuthProvider>
          <OfflineStatusProvider>
            <RouteRefreshProvider>
              <IBStatusProvider>
                <OrderActionsProvider>
                  <TickerDetailProvider>{children}</TickerDetailProvider>
                </OrderActionsProvider>
              </IBStatusProvider>
            </RouteRefreshProvider>
          </OfflineStatusProvider>
        </RealtimeAuthProvider>
      </ClerkThemeBridge>
    </ThemeProvider>
  );
}
