import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/hyad");

export default function RegimeHyAdPage() {
  return <WorkspaceShell section="regime" />;
}
