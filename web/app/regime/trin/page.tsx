import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/trin");

export default function RegimeTrinPage() {
  return <WorkspaceShell section="regime" />;
}
