"use client";

import { OrderActionsProvider } from "@/lib/OrderActionsContext";

export default function Providers({ children }: { children: React.ReactNode }) {
  return <OrderActionsProvider>{children}</OrderActionsProvider>;
}
