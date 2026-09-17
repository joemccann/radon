import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/credit");

export default function RegimeCreditPage() {
  return <WorkspaceShell section="regime" />;
}
