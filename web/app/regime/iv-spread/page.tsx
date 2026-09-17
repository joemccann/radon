import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/iv-spread");

export default function RegimeIvSpreadPage() {
  return <WorkspaceShell section="regime" />;
}
