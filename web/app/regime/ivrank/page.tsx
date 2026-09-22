import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/ivrank");

export default function RegimeIvRankPage() {
  return <WorkspaceShell section="regime" />;
}
