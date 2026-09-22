import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/bpi");

export default function RegimeBpiPage() {
  return <WorkspaceShell section="regime" />;
}
