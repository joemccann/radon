import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import Providers from "@/components/Providers";
import PwaRegister from "@/components/PwaRegister";
import ThemeBootstrap from "@/components/ThemeBootstrap";
import "./globals.css";

// Local files keep both page loads and production builds independent of the
// Google Fonts network. Variables preserve the existing typography contract.
const inter = localFont({
  src: "../public/fonts/Inter-Variable.woff2",
  weight: "100 900",
  variable: "--font-sans",
  display: "swap",
});

const plexMono = localFont({
  src: [
    { path: "../public/fonts/IBMPlexMono-400.woff2", weight: "400", style: "normal" },
    { path: "../public/fonts/IBMPlexMono-400-Italic.woff2", weight: "400", style: "italic" },
    { path: "../public/fonts/IBMPlexMono-500.woff2", weight: "500", style: "normal" },
    { path: "../public/fonts/IBMPlexMono-600.woff2", weight: "600", style: "normal" },
    { path: "../public/fonts/IBMPlexMono-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-mono",
  display: "swap",
});

// Inline custom properties outrank the :root fallbacks in globals.css. Using
// only next/font's variable classes leaves the winner dependent on CSS chunk
// order, which made production silently fall back to an unavailable family.
const fontVariables = {
  "--font-sans": inter.style.fontFamily,
  "--font-mono": plexMono.style.fontFamily,
} as React.CSSProperties;

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0f14" },
  ],
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
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${plexMono.variable}`}
      style={fontVariables}
    >
      <head>
        <ThemeBootstrap />
      </head>
      <body className="app-root">
        <Providers>{children}</Providers>
        <PwaRegister />
      </body>
    </html>
  );
}
