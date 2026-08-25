import WorkspaceShell from "@/components/WorkspaceShell";
import { readPortfolioSnapshotSeed } from "@/lib/portfolio/readPortfolioSnapshot.server";

export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  // Playwright owns /api/portfolio through browser route mocks. Avoid a direct
  // RSC DB read in that explicit authless harness so production and test data
  // sources cannot race each other.
  const initialPortfolio = process.env.RADON_AUTHLESS_TEST === "1"
    ? undefined
    : await readPortfolioSnapshotSeed();
  return <WorkspaceShell section="portfolio" initialPortfolio={initialPortfolio} />;
}
