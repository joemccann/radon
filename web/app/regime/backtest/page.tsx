import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/backtest");

export default function RegimeBacktestPage() {
  return <WorkspaceShell section="regime" />;
}
