"use client";

import { IBStatusProvider } from "@/lib/IBStatusContext";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import { ThemeProvider } from "@/lib/ThemeContext";
import { RealtimeAuthProvider } from "@/lib/RealtimeAuthContext";
import ClerkThemeBridge from "@/components/ClerkThemeBridge";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <ClerkThemeBridge>
        <RealtimeAuthProvider>
          <IBStatusProvider>
            <OrderActionsProvider>
              <TickerDetailProvider>{children}</TickerDetailProvider>
            </OrderActionsProvider>
          </IBStatusProvider>
        </RealtimeAuthProvider>
      </ClerkThemeBridge>
    </ThemeProvider>
  );
}
