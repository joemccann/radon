"use client";

import { ClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import { useTheme } from "@/lib/ThemeContext";

export default function ClerkThemeBridge({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  if (
    process.env.NEXT_PUBLIC_RADON_AUTHLESS_TEST === "1" ||
    process.env.RADON_AUTHLESS_TEST === "1"
  ) {
    return <>{children}</>;
  }
  return (
    <ClerkProvider appearance={{ theme: theme === "dark" ? dark : undefined }}>
      {children}
    </ClerkProvider>
  );
}
