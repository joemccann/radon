import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/high-low");

export default function RegimeHighLowPage() {
  return <WorkspaceShell section="regime" />;
}
