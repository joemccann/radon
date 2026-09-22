import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/short");

export default function RegimeShortPage() {
  return <WorkspaceShell section="regime" />;
}
