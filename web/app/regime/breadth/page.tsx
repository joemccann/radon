import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/breadth");

export default function RegimeBreadthPage() {
  return <WorkspaceShell section="regime" />;
}
