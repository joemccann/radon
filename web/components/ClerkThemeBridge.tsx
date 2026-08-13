"use client";

import { ClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import { useTheme } from "@/lib/ThemeContext";

export default function ClerkThemeBridge({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  return (
    <ClerkProvider appearance={{ theme: theme === "dark" ? dark : undefined }}>
      {children}
    </ClerkProvider>
  );
}
