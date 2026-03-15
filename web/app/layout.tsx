import type { Metadata } from "next";
import Providers from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Radon Terminal",
  description: "Market structure reconstruction instrument. Surfaces convex opportunities from institutional flow, volatility surfaces, and cross-asset positioning.",
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
    <html lang="en" data-theme="dark">
      <body className="app-root">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
