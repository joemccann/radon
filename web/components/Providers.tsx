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
// Clerk-only wrappers (ClerkThemeBridge, SignOutCachePurge) cannot mount —
// they need a ClerkProvider — but the rest of the tree, including the
// realtime prices provider, still must. Once keys exist this branch is dead.
const CLERK_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

export default function Providers({
  children,
  authlessTestBypass = false,
}: {
  children: React.ReactNode;
  authlessTestBypass?: boolean;
}) {
  // Mounted on EVERY boot path, Clerk configured or not — an early keyless
  // exit here once silently dropped the realtime tree (T-389).
  const core = (
    <RealtimeAuthProvider authlessTestBypass={authlessTestBypass}>
      <OfflineStatusProvider>
        <RouteRefreshProvider>
          <IBStatusProvider authlessTestBypass={authlessTestBypass}>
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
  );
  if (!CLERK_CONFIGURED) {
    return <ThemeProvider>{core}</ThemeProvider>;
  }
  return (
    <ThemeProvider>
      <ClerkThemeBridge>
        <SignOutCachePurge />
        {core}
      </ClerkThemeBridge>
    </ThemeProvider>
  );
}
