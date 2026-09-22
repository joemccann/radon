import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/dispersion");

export default function RegimeDispersionPage() {
  return <WorkspaceShell section="regime" />;
}
