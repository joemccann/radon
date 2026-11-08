import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/imbalance");

export default function RegimeImbalancePage() {
  return <WorkspaceShell section="regime" />;
}
