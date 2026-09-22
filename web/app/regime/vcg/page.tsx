import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vcg");

export default function RegimeVcgPage() {
  return <WorkspaceShell section="regime" />;
}
