import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/curve");

export default function RegimeCurvePage() {
  return <WorkspaceShell section="regime" />;
}
