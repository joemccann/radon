import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/cor");

export default function RegimeCorPage() {
  return <WorkspaceShell section="regime" />;
}
