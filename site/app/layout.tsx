import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { siteMetadata, siteStructuredData, siteViewport } from "../lib/seo";

export const metadata = siteMetadata;
export const viewport = siteViewport;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en-US">
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
