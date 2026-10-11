import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/record-high-breadth");

export default function RegimeRecordHighBreadthPage() {
  return <WorkspaceShell section="regime" />;
}
