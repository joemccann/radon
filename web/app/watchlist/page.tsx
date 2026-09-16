import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/watchlist");

export const dynamic = "force-dynamic";

export default function WatchlistPage() {
  return <WorkspaceShell section="watchlist" />;
}
