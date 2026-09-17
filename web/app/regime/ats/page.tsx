import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/ats");

export default function RegimeAtsPage() {
  return <WorkspaceShell section="regime" />;
}
