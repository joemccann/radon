import { Analytics } from "@vercel/analytics/next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { siteMetadata, siteStructuredData, siteViewport } from "../lib/seo";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  weight: ["400", "500", "700"],
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata = siteMetadata;
export const viewport = siteViewport;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en-US" className={`${inter.variable} ${ibmPlexMono.variable}`}>
      <body>
        {children}
        {siteStructuredData.map((item) => (
          <script
            key={item["@type"]}
            type="application/ld+json"
            dangerouslySetInnerHTML={{ __html: JSON.stringify(item) }}
          />
        ))}
        <Analytics />
      </body>
    </html>
  );
}
