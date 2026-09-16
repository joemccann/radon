import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/gex");

export default function RegimeGexPage() {
  return <WorkspaceShell section="regime" />;
}
