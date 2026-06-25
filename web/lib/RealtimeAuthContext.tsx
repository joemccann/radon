"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useAuth } from "@clerk/nextjs";

export type RealtimeTokenGetter = () => Promise<string | null>;

const RealtimeAuthContext = createContext<RealtimeTokenGetter | undefined>(undefined);

export function RealtimeAuthProvider({ children }: { children: ReactNode }) {
  if (
    process.env.NEXT_PUBLIC_RADON_AUTHLESS_TEST === "1" ||
    process.env.RADON_AUTHLESS_TEST === "1" ||
    process.env.NODE_ENV === "test"
  ) {
    return (
      <RealtimeAuthContext.Provider value={undefined}>
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
