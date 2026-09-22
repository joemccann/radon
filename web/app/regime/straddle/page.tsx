import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/straddle");

export default function RegimeStraddlePage() {
  return <WorkspaceShell section="regime" />;
}
