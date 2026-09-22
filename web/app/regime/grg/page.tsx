import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/grg");

export default function RegimeGrgPage() {
  return <WorkspaceShell section="regime" />;
}
