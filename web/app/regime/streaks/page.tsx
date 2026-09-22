import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/streaks");

export default function RegimeStreaksPage() {
  return <WorkspaceShell section="regime" />;
}
