"use client";

import { IBStatusProvider } from "@/lib/IBStatusContext";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import { ThemeProvider } from "@/lib/ThemeContext";
import { RealtimeAuthProvider } from "@/lib/RealtimeAuthContext";
import { RealtimePricesProvider } from "@/lib/RealtimePricesContext";
import { OfflineStatusProvider } from "@/lib/offline/OfflineStatusContext";
import { RouteRefreshProvider } from "@/lib/RouteRefreshContext";
import ClerkThemeBridge from "@/components/ClerkThemeBridge";
import SignOutCachePurge from "@/components/SignOutCachePurge";

// Inlined at build/dev start. In first-run setup mode (no Clerk keys) the
// Clerk provider tree cannot mount — ClerkProvider throws without a
// publishable key — and the middleware confines rendering to /setup, which
// needs only the theme. Once keys exist this branch is dead code.
const CLERK_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

export default function Providers({ children }: { children: React.ReactNode }) {
  if (!CLERK_CONFIGURED) {
    return <ThemeProvider>{children}</ThemeProvider>;
  }
  return (
    <ThemeProvider>
      <ClerkThemeBridge>
        <SignOutCachePurge />
        <RealtimeAuthProvider>
          <OfflineStatusProvider>
            <RouteRefreshProvider>
              <IBStatusProvider>
                {/* Owns the realtime prices socket for the life of the tab.
                    Lives here (not in the per-page WorkspaceShell) so App
                    Router navigations never tear the connection down. */}
                <RealtimePricesProvider>
                  <OrderActionsProvider>
                    <TickerDetailProvider>{children}</TickerDetailProvider>
                  </OrderActionsProvider>
                </RealtimePricesProvider>
              </IBStatusProvider>
            </RouteRefreshProvider>
          </OfflineStatusProvider>
        </RealtimeAuthProvider>
      </ClerkThemeBridge>
    </ThemeProvider>
  );
}
