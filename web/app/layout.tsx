import type { Metadata, Viewport } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import Providers from "@/components/Providers";
import PwaRegister from "@/components/PwaRegister";
import "./globals.css";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: "#0a0f14",
};

export const metadata: Metadata = {
  title: "Radon Terminal",
  description: "Market structure reconstruction instrument. Surfaces convex opportunities from institutional flow, volatility surfaces, and cross-asset positioning.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Radon",
  },
  icons: {
    icon: [
      { url: "/icons/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/icons/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/favicon-64x64.png", sizes: "64x64", type: "image/png" },
    ],
    apple: "/icons/apple-touch-icon.png",
  },
  openGraph: {
    title: "Radon Terminal",
    description: "Reconstructing market structure from noisy signals.",
    images: [
      {
        url: "/images/hero-og.png",
        width: 1200,
        height: 630,
        alt: "Radon Terminal - Market Structure Reconstruction",
      },
      {
        url: "/images/markov-og.png",
        width: 1200,
        height: 630,
        alt: "Radon Terminal - Markov State Reconstruction",
      },
    ],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider appearance={{ baseTheme: dark }}>
      <html lang="en" data-theme="dark">
        <body className="app-root">
          <Providers>{children}</Providers>
          <PwaRegister />
        </body>
      </html>
    </ClerkProvider>
  );
}
