import { notFound, redirect } from "next/navigation";
import WorkspaceShell from "@/components/WorkspaceShell";

// Static routes that Next.js already handles — defense-in-depth guard
const RESERVED = new Set([
  "api", "dashboard", "flow-analysis", "portfolio", "performance",
  "orders", "scanner", "discover", "watchlist", "journal", "regime", "cta", "kit",
  "internals",
  "_next", "favicon",
]);

const TICKER_RE = /^[A-Za-z]{1,5}$/;

type Props = {
  params: Promise<{ ticker: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function TickerPage({ params, searchParams }: Props) {
  const { ticker: raw } = await params;
  const sp = await searchParams;

  // Guard reserved paths (static routes already win, but be explicit)
  if (RESERVED.has(raw.toLowerCase())) return notFound();

  // Format validation: 1-5 alpha chars only
  if (!TICKER_RE.test(raw)) return notFound();

  // Canonical URL is uppercase — redirect if not
  const upper = raw.toUpperCase();
  if (raw !== upper) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(sp)) {
      if (Array.isArray(value)) {
        value.forEach((item) => params.append(key, item));
      } else if (value != null) {
        params.set(key, value);
      }
    }
    const qs = params.toString();
    redirect(`/${upper}${qs ? `?${qs}` : ""}`);
  }

  return (
    <WorkspaceShell
      section="ticker-detail"
      tickerParam={upper}
    />
  );
}
