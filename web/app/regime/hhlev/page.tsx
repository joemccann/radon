import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/hhlev");

export default function RegimeHhLevPage() {
  return <WorkspaceShell section="regime" />;
}
