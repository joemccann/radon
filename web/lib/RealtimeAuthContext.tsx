"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useAuth } from "@clerk/nextjs";

export type RealtimeTokenGetter = () => Promise<string | null>;

const RealtimeAuthContext = createContext<RealtimeTokenGetter | undefined>(undefined);

// Inlined at build/dev start. In first-run setup mode (no Clerk keys) there is
// no ClerkProvider, so useAuth() would throw; hand out an anonymous getter.
const CLERK_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

const anonymousToken: RealtimeTokenGetter = async () => null;

export function RealtimeAuthProvider({ children }: { children: ReactNode }) {
  if (!CLERK_CONFIGURED) {
    return (
      <RealtimeAuthContext.Provider value={anonymousToken}>
        {children}
      </RealtimeAuthContext.Provider>
    );
  }
  return <ClerkRealtimeAuthProvider>{children}</ClerkRealtimeAuthProvider>;
}

function ClerkRealtimeAuthProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  return (
    <RealtimeAuthContext.Provider value={getToken}>
      {children}
    </RealtimeAuthContext.Provider>
  );
}

export function useRealtimeAuth(): RealtimeTokenGetter | undefined {
  return useContext(RealtimeAuthContext);
}
