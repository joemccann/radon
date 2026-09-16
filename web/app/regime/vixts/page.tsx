import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vixts");

export default function RegimeVixTsPage() {
  return <WorkspaceShell section="regime" />;
}
