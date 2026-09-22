import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/ma-ratio");

export default function RegimeMaRatioPage() {
  return <WorkspaceShell section="regime" />;
}
