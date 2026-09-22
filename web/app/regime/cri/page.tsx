import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/cri");

export default function RegimeCriPage() {
  return <WorkspaceShell section="regime" />;
}
