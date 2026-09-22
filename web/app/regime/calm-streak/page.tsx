import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/calm-streak");

export default function RegimeCalmStreakPage() {
  return <WorkspaceShell section="regime" />;
}
